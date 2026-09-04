use chrono::{DateTime, Datelike, Duration as ChronoDuration, NaiveDate, NaiveTime, TimeZone, Utc};
use chrono_tz::America::New_York;
use qmd_core::config::{load_env_files, GatewayConfig};
use qmd_core::generic_structure::{GenericStructureEngine, GENERIC_STRUCTURE_ALGORITHM_VERSION};
use qmd_core::indicators::{DailyStructureCheckpoint, IndicatorClickHouseWriter};
use qmd_core::metrics::SharedMetrics;
use qmd_core::structure_certification::{
    build_checkpoint_certification, build_recovered_checkpoint_certification, checkpoint_sha256,
    validate_checkpoint_certification, StructureCheckpointRecoveryAttestation,
    StructureEventAuditor,
};
use qmd_history_gateway::config::HistoricalGatewayConfig;
use qmd_history_gateway::source::{
    HistoricalEventSource, StructureCampaignTicker, StructureEventCountEstimateRequest,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashSet, VecDeque};
use std::env;
use std::fs;
use std::io::{IsTerminal, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::{Mutex, Notify};
use tokio::task::JoinSet;

const EVENT_RATE_WINDOW_SECONDS: i64 = 300;
const EVENT_RATE_MIN_SAMPLE_SECONDS: i64 = 15;
const INITIAL_ORDINAL_CHUNK: u64 = 250_000;
const MIN_ORDINAL_CHUNK: u64 = 100_000;
const MAX_ORDINAL_CHUNK: u64 = 1_000_000;
const TARGET_FETCH_MILLIS: u128 = 3_000;
const MAX_WORKERS: usize = 80;
const CAMPAIGN_STOP_REQUESTED: &str = "campaign stop requested";
static STATUS_WRITE_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Debug)]
struct Args {
    checkpoint_set_id: String,
    recovery_source_checkpoint_set_id: Option<String>,
    core_index: Option<usize>,
    explicit_universe_only: bool,
    register_set_state: Option<String>,
    set_event_count: u64,
    set_ticker_count: u64,
    set_universe_hash: Option<String>,
    shard_worker: bool,
    campaign_control_path: Option<PathBuf>,
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
    certified: usize,
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
    checkpoint_set_id: String,
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
    checkpoint_set_id: String,
    campaign_control_path: Option<PathBuf>,
    inner: Mutex<Progress>,
    abort_requested: AtomicBool,
    processed_events: AtomicU64,
    status_notify: Notify,
}

impl ProgressWriter {
    fn new(
        path: PathBuf,
        campaign_control_path: Option<PathBuf>,
        checkpoint_set_id: String,
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
            checkpoint_set_id: checkpoint_set_id.clone(),
            campaign_control_path,
            abort_requested: AtomicBool::new(false),
            processed_events: AtomicU64::new(0),
            status_notify: Notify::new(),
            inner: Mutex::new(Progress {
                schema_version: 7,
                status: "running".to_string(),
                started_at: Utc::now(),
                updated_at: Utc::now(),
                algorithm_version: GENERIC_STRUCTURE_ALGORITHM_VERSION,
                checkpoint_set_id,
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
            "completed" => {
                progress.counts.completed += 1;
                progress.counts.certified += 1;
            }
            "skipped" => {
                progress.counts.skipped += 1;
                progress.counts.certified += 1;
            }
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
        self.retries(1).await
    }

    async fn retries(&self, count: usize) -> Result<(), String> {
        let mut progress = self.inner.lock().await;
        progress.counts.retried = progress.counts.retried.saturating_add(count);
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
        let sequence = STATUS_WRITE_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let temporary =
            self.path
                .with_extension(format!("json.{}.{}.tmp", std::process::id(), sequence));
        fs::write(&temporary, bytes)
            .map_err(|error| format!("failed to write {}: {error}", temporary.display()))?;
        let deadline = Instant::now() + Duration::from_secs(5);
        let mut delay = Duration::from_millis(20);
        loop {
            match fs::rename(&temporary, &self.path) {
                Ok(()) => return Ok(()),
                Err(error)
                    if error.kind() == std::io::ErrorKind::PermissionDenied
                        && Instant::now() < deadline =>
                {
                    std::thread::sleep(delay);
                    delay = (delay * 2).min(Duration::from_millis(500));
                }
                Err(error) => {
                    let _ = fs::remove_file(&temporary);
                    return Err(format!(
                        "failed to publish {}: {error}",
                        self.path.display()
                    ));
                }
            }
        }
    }

    fn record_events(&self, count: u64) {
        self.processed_events.fetch_add(count, Ordering::Relaxed);
    }

    fn rollback_events(&self, count: u64) {
        self.processed_events.fetch_sub(count, Ordering::Relaxed);
    }

    fn request_abort(&self) {
        self.abort_requested.store(true, Ordering::Release);
    }

    fn abort_requested(&self) -> bool {
        self.abort_requested.load(Ordering::Acquire)
    }

    fn requested_stop_mode(&self) -> Option<StopMode> {
        let path = self.campaign_control_path.as_ref()?;
        let bytes = fs::read(path).ok()?;
        let request = serde_json::from_slice::<CampaignControl>(&bytes).ok()?;
        if request.schema_version != 1 || request.checkpoint_set_id != self.checkpoint_set_id {
            return None;
        }
        match request.action.as_str() {
            "stop_fast" => Some(StopMode::Fast),
            "stop_graceful" => Some(StopMode::Graceful),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum StopMode {
    Graceful,
    Fast,
}

#[derive(Debug, Deserialize)]
struct CampaignControl {
    schema_version: u16,
    checkpoint_set_id: String,
    action: String,
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
    persistence_retries: usize,
    checkpoint_sha256: String,
    chain_sha256: String,
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

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let args = parse_args().map_err(io_error)?;
    pin_current_thread(args.core_index).map_err(io_error)?;
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
    let mut history_config = HistoricalGatewayConfig::from_env();
    history_config.structure_checkpoint_set_id = args.checkpoint_set_id.clone();
    history_config.validate().map_err(io_error)?;
    let source = HistoricalEventSource::initialize(history_config.clone())
        .await
        .map_err(io_error)?;
    let mut gateway_config = GatewayConfig::from_env();
    gateway_config.structure_checkpoint_set_id = args.checkpoint_set_id.clone();
    let writer = IndicatorClickHouseWriter::new(gateway_config, SharedMetrics::new());
    writer.initialize().await.map_err(io_error)?;
    if let Some(state) = args.register_set_state.as_deref() {
        let universe_hash = args
            .set_universe_hash
            .as_deref()
            .ok_or_else(|| io_error("--set-universe-hash is required with --register-set-state"))?;
        let checkpoint_count = writer
            .count_daily_structure_checkpoints_in_set()
            .await
            .map_err(io_error)?;
        let certified_checkpoint_count = writer
            .count_certified_daily_structure_checkpoints_in_set()
            .await
            .map_err(io_error)?;
        if state == "sealed" && certified_checkpoint_count != checkpoint_count {
            return Err(io_error(format!(
                "refusing to seal checkpoint set with {certified_checkpoint_count} certified row(s) out of {checkpoint_count} durable row(s)"
            )));
        }
        writer
            .persist_structure_checkpoint_set_state(
                args.start_date,
                args.end_date,
                universe_hash,
                state,
                args.set_ticker_count,
                checkpoint_count,
                certified_checkpoint_count,
                args.set_event_count,
            )
            .await
            .map_err(io_error)?;
        println!(
            "Checkpoint set {} registered as {} with {} durable checkpoint row(s).",
            args.checkpoint_set_id, state, checkpoint_count
        );
        return Ok(());
    }
    let automatic_tickers = if args.explicit_universe_only {
        Vec::new()
    } else {
        source
            .structure_campaign_tickers(
                args.start_date,
                args.liquidity_start_date,
                args.liquidity_end_date,
                Utc::now(),
            )
            .await
            .map_err(io_error)?
    };
    let file_tickers = load_tickers(&args.ticker_files).map_err(io_error)?;
    let tickers = merge_ticker_universe(&args.priority_tickers, &file_tickers, &automatic_tickers)
        .map_err(io_error)?;
    if tickers.is_empty() {
        return Err(io_error("ticker universe is empty"));
    }
    if args.purge_existing_checkpoints {
        let deleted = writer
            .purge_daily_structure_checkpoint_set()
            .await
            .map_err(io_error)?;
        eprintln!(
            "Removed {deleted} pre-existing row(s) from checkpoint set {}; this campaign will rebuild that set cold.",
            args.checkpoint_set_id
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
            "Validated Campaign v7 plan: tickers={} units={} plan={}",
            plans.len(),
            plans.iter().map(|plan| plan.sessions.len()).sum::<usize>(),
            plan_path.display()
        );
        return Ok(());
    }
    let universe_hash = campaign_universe_hash(&plans);
    let plan_ticker_count = plans.len() as u64;
    if !args.shard_worker {
        writer
            .persist_structure_checkpoint_set_state(
                args.start_date,
                args.end_date,
                &universe_hash,
                "building",
                plan_ticker_count,
                0,
                0,
                0,
            )
            .await
            .map_err(io_error)?;
    }
    let progress_path = args.runtime_dir.join("campaign-status.json");
    let progress = Arc::new(ProgressWriter::new(
        progress_path.clone(),
        args.campaign_control_path.clone(),
        args.checkpoint_set_id.clone(),
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
        let checkpoint_set_id = args.checkpoint_set_id.clone();
        let recovery_source_checkpoint_set_id = args.recovery_source_checkpoint_set_id.clone();
        tasks.spawn(async move {
            let mut errors = Vec::new();
            loop {
                if progress.abort_requested() || progress.requested_stop_mode().is_some() {
                    break;
                }
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
                    &checkpoint_set_id,
                    recovery_source_checkpoint_set_id.as_deref(),
                )
                .await
                {
                    if error == CAMPAIGN_STOP_REQUESTED {
                        progress.request_abort();
                        break;
                    }
                    if campaign_fatal_error(&error) {
                        progress.request_abort();
                    }
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
    interrupted |= progress.requested_stop_mode().is_some();
    let final_progress = if interrupted {
        progress.interrupt().await.map_err(io_error)?
    } else {
        progress.complete(task_failed).await.map_err(io_error)?
    };
    let checkpoint_count = writer
        .count_daily_structure_checkpoints_in_set()
        .await
        .unwrap_or_default();
    let certified_checkpoint_count = writer
        .count_certified_daily_structure_checkpoints_in_set()
        .await
        .unwrap_or_default();
    let set_state = if interrupted {
        "interrupted"
    } else if task_failed
        || final_progress.counts.failed > 0
        || certified_checkpoint_count != checkpoint_count
    {
        "failed"
    } else {
        "sealed"
    };
    let set_event_count = if set_state == "sealed" {
        final_progress.total_estimated_events
    } else {
        final_progress.events_processed
    };
    if !args.shard_worker {
        writer
            .persist_structure_checkpoint_set_state(
                args.start_date,
                args.end_date,
                &universe_hash,
                set_state,
                plan_ticker_count,
                checkpoint_count,
                certified_checkpoint_count,
                set_event_count,
            )
            .await
            .map_err(io_error)?;
    }
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
                "Checkpoint Campaign v7 | {} | v{} | {} workers",
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
                "Durable {} | certified {} | current {} | retries {} | failed {}",
                format_count(progress.counts.completed as u64),
                format_count(progress.counts.certified as u64),
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
                "Structural Checkpoint Campaign v7 | {} | algorithm v{} | workers {}",
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
                "Durable {:>8} | certified {:>8} | current {:>8} | retries {:>6} | unavailable {:>6} | failed {:>4} | blocked {:>6}",
                format_count(progress.counts.completed as u64),
                format_count(progress.counts.certified as u64),
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

fn validated_current_checkpoint_prefix(
    manifest: &qmd_history_gateway::source::StructureCampaignManifest,
    rows: Vec<DailyStructureCheckpoint>,
) -> Result<Option<DailyStructureCheckpoint>, String> {
    let rows = rows
        .into_iter()
        .map(|row| (row.session_date, row))
        .collect::<BTreeMap<_, _>>();
    let mut predecessor_checkpoint = String::new();
    let mut predecessor_chain = String::new();
    let mut prefix = None;
    for session in &manifest.sessions {
        let Some(row) = rows.get(&session.session_date) else {
            break;
        };
        let Some(certification) = row.certification.as_ref() else {
            break;
        };
        if row.authority_start != manifest.authority_start
            || row.source_plan_hash != session.source_revision.source_plan_hash
            || row.source_revision_token != session.source_revision.token
            || certification.predecessor_checkpoint_sha256 != predecessor_checkpoint
            || certification.predecessor_chain_sha256 != predecessor_chain
        {
            break;
        }
        if validate_checkpoint_certification(
            certification,
            &row.checkpoint,
            row.session_date,
            row.authority_start,
            &row.source_plan_hash,
            &row.source_revision_token,
        )
        .is_err()
        {
            break;
        }
        predecessor_checkpoint = certification.checkpoint_sha256.clone();
        predecessor_chain = certification.chain_sha256.clone();
        prefix = Some(row.clone());
    }
    Ok(prefix)
}

async fn recover_reusable_checkpoint_prefix(
    writer: &IndicatorClickHouseWriter,
    manifest: &qmd_history_gateway::source::StructureCampaignManifest,
    target_set_id: &str,
    source_set_id: &str,
) -> Result<(), String> {
    let first_session = manifest
        .sessions
        .first()
        .ok_or_else(|| "checkpoint recovery has no sessions".to_string())?
        .session_date;
    let last_session = manifest
        .sessions
        .last()
        .ok_or_else(|| "checkpoint recovery has no sessions".to_string())?
        .session_date;
    let source_rows = writer
        .load_daily_structure_checkpoint_chain_from_set(
            source_set_id,
            &manifest.ticker,
            first_session,
            last_session,
        )
        .await?
        .into_iter()
        .map(|row| (row.session_date, row))
        .collect::<BTreeMap<_, _>>();
    let mut source_predecessor_checkpoint = String::new();
    let mut source_predecessor_chain = String::new();
    let mut target_predecessor_checkpoint = String::new();
    let mut target_predecessor_chain = String::new();
    let mut prior_migrated_checkpoint = None;
    for session in &manifest.sessions {
        let Some(row) = source_rows.get(&session.session_date) else {
            break;
        };
        let Some(source_certification) = row.certification.as_ref() else {
            break;
        };
        if row.authority_start != manifest.authority_start
            || source_certification.predecessor_checkpoint_sha256 != source_predecessor_checkpoint
            || source_certification.predecessor_chain_sha256 != source_predecessor_chain
        {
            break;
        }
        if validate_checkpoint_certification(
            source_certification,
            &row.checkpoint,
            row.session_date,
            row.authority_start,
            &row.source_plan_hash,
            &row.source_revision_token,
        )
        .is_err()
        {
            break;
        }
        source_predecessor_checkpoint = source_certification.checkpoint_sha256.clone();
        source_predecessor_chain = source_certification.chain_sha256.clone();

        // Execution-aware archive checkpoints intentionally excluded reports
        // that the historical SIP-plus-condition policy admits. They are a
        // different lineage and must be replayed, never relabeled into v7.
        if row.source_revision_token.contains(":execution-clock-v1:") {
            break;
        }

        let migrated_checkpoint = GenericStructureEngine::migrate_checkpoint_derived_projections(
            &row.checkpoint,
            prior_migrated_checkpoint.as_ref(),
            row.session_date,
        )?;

        let certification = if row
            .source_revision_token
            .contains(":structure-input-v1:archive-sip-condition:")
        {
            build_checkpoint_certification(
                &migrated_checkpoint,
                source_certification.event_evidence.clone(),
                row.session_date,
                row.authority_start,
                &session.source_revision.source_plan_hash,
                &session.source_revision.token,
                target_predecessor_checkpoint.clone(),
                target_predecessor_chain.clone(),
            )?
        } else {
            build_recovered_checkpoint_certification(
                &migrated_checkpoint,
                source_certification.event_evidence.clone(),
                row.session_date,
                row.authority_start,
                &session.source_revision.source_plan_hash,
                &session.source_revision.token,
                target_predecessor_checkpoint.clone(),
                target_predecessor_chain.clone(),
                StructureCheckpointRecoveryAttestation {
                    recovery_revision: "historical-sip-condition-recertification-v1".to_string(),
                    source_checkpoint_set_id: source_set_id.to_string(),
                    source_checkpoint_sha256: source_certification.checkpoint_sha256.clone(),
                    source_chain_sha256: source_certification.chain_sha256.clone(),
                    source_policy_revision: session.source_revision.token.clone(),
                    execution_clock_revision: String::new(),
                    delayed_trade_report_count: 0,
                },
            )?
        };
        writer
            .persist_daily_structure_checkpoint_with_retries(&DailyStructureCheckpoint {
                checkpoint_set_id: target_set_id.to_string(),
                session_date: row.session_date,
                algorithm_version: row.algorithm_version,
                sym: row.sym.clone(),
                authority_start: row.authority_start,
                checkpoint_at: row.checkpoint_at,
                last_arrival_sequence: row.last_arrival_sequence,
                source_plan_hash: session.source_revision.source_plan_hash.clone(),
                source_revision_token: session.source_revision.token.clone(),
                source_complete: true,
                built_at: Utc::now(),
                checkpoint: migrated_checkpoint.clone(),
                certification: Some(certification.clone()),
            })
            .await?;
        target_predecessor_checkpoint = certification.checkpoint_sha256;
        target_predecessor_chain = certification.chain_sha256;
        prior_migrated_checkpoint = Some(migrated_checkpoint);
    }
    Ok(())
}

async fn run_ticker(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    writer: &IndicatorClickHouseWriter,
    progress: &ProgressWriter,
    plan: TickerPlan,
    max_retries: usize,
    retry_delay_seconds: u64,
    checkpoint_set_id: &str,
    recovery_source_checkpoint_set_id: Option<&str>,
) -> Result<(), String> {
    let manifest = source
        .structure_campaign_manifest(&plan.ticker, plan.rebuild_start, &plan.sessions)
        .await?;
    if let Some(source_set_id) = recovery_source_checkpoint_set_id {
        recover_reusable_checkpoint_prefix(writer, &manifest, checkpoint_set_id, source_set_id)
            .await?;
    }
    let target_rows = writer
        .load_daily_structure_checkpoint_chain_from_set(
            checkpoint_set_id,
            &plan.ticker,
            *plan
                .sessions
                .first()
                .ok_or_else(|| "campaign has no sessions".to_string())?,
            *plan
                .sessions
                .last()
                .ok_or_else(|| "campaign has no sessions".to_string())?,
        )
        .await?;
    let seed = validated_current_checkpoint_prefix(&manifest, target_rows)?;
    let seed_session = seed.as_ref().map(|row| row.session_date);
    let seed_cursor = seed
        .as_ref()
        .map(|row| row.last_arrival_sequence)
        .unwrap_or_default();
    let mut predecessor_checkpoint_sha256 = seed
        .as_ref()
        .map(|row| checkpoint_sha256(&row.checkpoint))
        .transpose()?
        .unwrap_or_default();
    let mut predecessor_chain_sha256 = seed
        .as_ref()
        .and_then(|row| row.certification.as_ref())
        .map(|certification| certification.chain_sha256.clone())
        .unwrap_or_default();
    let mut engine = GenericStructureEngine::new(&plan.ticker);
    if let Some(seed) = seed {
        engine.seed_checkpoint(&seed.checkpoint);
    }
    let rules = source.trade_aggregation_rules();

    for session in &manifest.sessions {
        if progress.requested_stop_mode().is_some() {
            return Err(CAMPAIGN_STOP_REQUESTED.to_string());
        }
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
                predecessor_checkpoint_sha256.clone(),
                predecessor_chain_sha256.clone(),
            )
            .await;
            match build_result {
                Ok(result)
                    if result.event_count == event_progress.observed()
                        && result.event_count == session.event_count =>
                {
                    progress.retries(result.persistence_retries).await?;
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
                predecessor_checkpoint_sha256 = result.checkpoint_sha256.clone();
                predecessor_chain_sha256 = result.chain_sha256.clone();
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
    predecessor_checkpoint_sha256: String,
    predecessor_chain_sha256: String,
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
    let mut event_count = 0_u64;
    let mut advanced_event_count = 0_u64;
    let mut prior_sip = 0_u64;
    let mut first_sip = 0_u64;
    let mut last_sip = 0_u64;
    let mut first_ordinal = session.first_ordinal;
    let mut ordinal_chunk = INITIAL_ORDINAL_CHUNK;
    let mut event_auditor = StructureEventAuditor::new(true);
    while first_ordinal < session.next_ordinal {
        if event_progress.campaign.requested_stop_mode() == Some(StopMode::Fast) {
            return Err(CAMPAIGN_STOP_REQUESTED.to_string());
        }
        let next_ordinal = first_ordinal
            .saturating_add(ordinal_chunk)
            .min(session.next_ordinal);
        let fetch_started = Instant::now();
        let mut batches = source.stream_structure_ordinal_range(
            session_date,
            &manifest.ticker,
            first_ordinal,
            next_ordinal,
            config.batch_size,
        )?;
        let mut buffered = Vec::with_capacity((next_ordinal - first_ordinal) as usize);
        while let Some(batch) = batches.recv().await {
            buffered.extend(batch?);
        }
        let fetch_millis = fetch_started.elapsed().as_millis().max(1);
        for compact in &buffered {
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
            event_auditor.observe(compact)?;
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
        event_progress.record(buffered.len() as u64);
        first_ordinal = next_ordinal;
        ordinal_chunk = next_ordinal_chunk(ordinal_chunk, fetch_millis);
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
    validate_daily_structure_source_revision(
        revision.complete_for_history,
        revision.request_complete,
        &revision.source_plan_hash,
        &revision.token,
    )?;
    let event_evidence = event_auditor.finish();
    if event_evidence.event_count != event_count
        || (event_count > 0
            && (event_evidence.first_arrival_sequence != session.first_ordinal
                || event_evidence.last_arrival_sequence.saturating_add(1) != session.next_ordinal
                || event_evidence.ordinal_contiguous != Some(true)))
    {
        return Err(format!(
            "daily checkpoint certification evidence does not cover the exact ordinal range for {} {}",
            manifest.ticker, session_date,
        ));
    }
    let certification = build_checkpoint_certification(
        &checkpoint,
        event_evidence,
        session_date,
        manifest.authority_start,
        &revision.source_plan_hash,
        &revision.token,
        predecessor_checkpoint_sha256,
        predecessor_chain_sha256,
    )?;
    let checkpoint_hash = certification.checkpoint_sha256.clone();
    let chain_hash = certification.chain_sha256.clone();
    let persistence_retries = writer
        .persist_daily_structure_checkpoint_with_retries(&DailyStructureCheckpoint {
            checkpoint_set_id: config.structure_checkpoint_set_id.clone(),
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
            certification: Some(certification),
        })
        .await?;
    Ok(DayResult {
        status: "completed",
        event_count,
        advanced_event_count,
        cursor: checkpoint.last_arrival_sequence,
        persistence_retries,
        checkpoint_sha256: checkpoint_hash,
        chain_sha256: chain_hash,
    })
}

fn next_ordinal_chunk(current: u64, fetch_millis: u128) -> u64 {
    if fetch_millis == 0 {
        return current;
    }
    let scaled = (current as u128)
        .saturating_mul(TARGET_FETCH_MILLIS)
        .checked_div(fetch_millis)
        .unwrap_or(current as u128);
    let lower = (current / 2).max(MIN_ORDINAL_CHUNK) as u128;
    let upper = current.saturating_mul(2).min(MAX_ORDINAL_CHUNK) as u128;
    scaled.clamp(lower, upper) as u64
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
        "connection closed",
        "broken pipe",
        "error sending request for url",
        "request failed before a confirmed response",
        "unexpected eof",
        "error decoding response body",
        "temporarily unavailable",
        "memory limit",
        "too many simultaneous queries",
    ]
    .iter()
    .any(|marker| error.contains(marker))
}

fn campaign_fatal_error(error: &str) -> bool {
    let error = error.to_ascii_lowercase();
    [
        "no_common_type",
        "unknown_identifier",
        "unknown_table",
        "syntax_error",
        "cannot_parse",
        "invalid structure continuity row",
        "invalid structure split row",
        "serialized payload hash drifted",
        "daily checkpoint source revision is not historical-sip-condition-v1",
    ]
    .iter()
    .any(|marker| error.contains(marker))
}

fn validate_daily_structure_source_revision(
    complete_for_history: bool,
    request_complete: bool,
    source_plan_hash: &str,
    token: &str,
) -> Result<(), String> {
    if !complete_for_history
        || !request_complete
        || source_plan_hash.trim().is_empty()
        || token.trim().is_empty()
    {
        return Err("daily checkpoint authority is incomplete".to_string());
    }
    if !token.contains(":structure-input-v1:archive-sip-condition:") {
        return Err(
            "daily checkpoint source revision is not historical-sip-condition-v1".to_string(),
        );
    }
    Ok(())
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

fn campaign_universe_hash(plans: &[TickerPlan]) -> String {
    let mut digest = Sha256::new();
    digest.update(format!(
        "algorithm={}\n",
        GENERIC_STRUCTURE_ALGORITHM_VERSION
    ));
    for plan in plans {
        digest.update(plan.ticker.as_bytes());
        digest.update(b"\n");
    }
    format!("{:x}", digest.finalize())
}

#[cfg(windows)]
fn pin_current_thread(core_index: Option<usize>) -> Result<(), String> {
    use std::ffi::c_void;

    #[repr(C)]
    struct GroupAffinity {
        mask: usize,
        group: u16,
        reserved: [u16; 3],
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn GetActiveProcessorGroupCount() -> u16;
        fn GetActiveProcessorCount(group_number: u16) -> u32;
        fn GetCurrentThread() -> *mut c_void;
        fn SetThreadGroupAffinity(
            thread: *mut c_void,
            group_affinity: *const GroupAffinity,
            previous_group_affinity: *mut GroupAffinity,
        ) -> i32;
    }

    let Some(mut remaining) = core_index else {
        return Ok(());
    };
    unsafe {
        let groups = GetActiveProcessorGroupCount();
        for group in 0..groups {
            let count = GetActiveProcessorCount(group) as usize;
            if remaining < count {
                if remaining >= usize::BITS as usize {
                    return Err(format!(
                        "core index {remaining} exceeds processor-group mask"
                    ));
                }
                let affinity = GroupAffinity {
                    mask: 1usize << remaining,
                    group,
                    reserved: [0; 3],
                };
                if SetThreadGroupAffinity(GetCurrentThread(), &affinity, std::ptr::null_mut()) == 0
                {
                    return Err(format!(
                        "failed to pin campaign worker to processor group {group} core {remaining}: {}",
                        std::io::Error::last_os_error()
                    ));
                }
                return Ok(());
            }
            remaining -= count;
        }
    }
    Err(format!("core index {} is unavailable", core_index.unwrap()))
}

#[cfg(not(windows))]
fn pin_current_thread(_core_index: Option<usize>) -> Result<(), String> {
    Ok(())
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
    let mut checkpoint_set_id = None;
    let mut recovery_source_checkpoint_set_id = None;
    let mut core_index = None;
    let mut explicit_universe_only = false;
    let mut register_set_state = None;
    let mut set_event_count = 0_u64;
    let mut set_ticker_count = 0_u64;
    let mut set_universe_hash = None;
    let mut shard_worker = false;
    let mut campaign_control_path = None;
    while let Some(argument) = values.next() {
        let value = |name: &str, values: &mut std::iter::Skip<std::env::Args>| {
            values
                .next()
                .ok_or_else(|| format!("{name} requires a value"))
        };
        match argument.as_str() {
            "--ticker-file" => ticker_files.push(PathBuf::from(value(&argument, &mut values)?)),
            "--checkpoint-set-id" => checkpoint_set_id = Some(value(&argument, &mut values)?),
            "--recovery-source-checkpoint-set-id" => {
                recovery_source_checkpoint_set_id = Some(value(&argument, &mut values)?)
            }
            "--core-index" => {
                core_index = Some(parse_number(&argument, &value(&argument, &mut values)?)?)
            }
            "--explicit-universe-only" => explicit_universe_only = true,
            "--shard-worker" => shard_worker = true,
            "--campaign-control-path" => {
                campaign_control_path = Some(PathBuf::from(value(&argument, &mut values)?))
            }
            "--register-set-state" => register_set_state = Some(value(&argument, &mut values)?),
            "--set-universe-hash" => set_universe_hash = Some(value(&argument, &mut values)?),
            "--set-event-count" => {
                set_event_count = parse_number(&argument, &value(&argument, &mut values)?)?
            }
            "--set-ticker-count" => {
                set_ticker_count = parse_number(&argument, &value(&argument, &mut values)?)?
            }
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
                println!("structure-checkpoint-campaign v7");
                println!("  --start-date YYYY-MM-DD --end-date YYYY-MM-DD");
                println!("  --checkpoint-set-id ID");
                println!("  [--recovery-source-checkpoint-set-id ID]");
                println!("  --runtime-dir PATH [--workers 4]  # allowed: 1-{MAX_WORKERS}");
                println!("  [--priority-ticker SUGP] [--ticker-file PATH]");
                println!("  [--liquidity-start-date YYYY-MM-DD --liquidity-end-date YYYY-MM-DD]");
                println!("  [--max-retries 5] [--retry-delay-seconds 2]");
                println!("  [--purge-existing-checkpoints] [--plan-only]");
                println!("  [--explicit-universe-only] [--core-index N]");
                println!("  [--campaign-control-path PATH]  # supervisor-owned stop control");
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
    let checkpoint_set_id =
        checkpoint_set_id.ok_or_else(|| "--checkpoint-set-id is required".to_string())?;
    validate_checkpoint_set_id(&checkpoint_set_id)?;
    if let Some(source_set_id) = recovery_source_checkpoint_set_id.as_deref() {
        validate_checkpoint_set_id(source_set_id)?;
        if source_set_id == checkpoint_set_id {
            return Err("recovery source and target checkpoint sets must differ".to_string());
        }
    }
    if register_set_state
        .as_deref()
        .is_some_and(|state| !matches!(state, "building" | "sealed" | "failed" | "interrupted"))
    {
        return Err(
            "--register-set-state must be building, sealed, failed, or interrupted".to_string(),
        );
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
        checkpoint_set_id,
        recovery_source_checkpoint_set_id,
        core_index,
        explicit_universe_only,
        register_set_state,
        set_event_count,
        set_ticker_count,
        set_universe_hash,
        shard_worker,
        campaign_control_path,
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

fn validate_checkpoint_set_id(value: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > 128
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
    {
        return Err("--checkpoint-set-id must contain only letters, digits, '.', '-', or '_' and be at most 128 bytes".to_string());
    }
    Ok(())
}

fn validate_worker_count(workers: usize) -> Result<(), String> {
    if !(1..=MAX_WORKERS).contains(&workers) {
        return Err(format!("--workers must be between 1 and {MAX_WORKERS}"));
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
        campaign_fatal_error, dashboard_frame, dashboard_lines, insert_ticker, log_snapshot,
        merge_ticker_universe, next_ordinal_chunk, retryable_error, session_is_covered_by_seed,
        validate_checkpoint_set_id, validate_daily_structure_source_revision,
        validate_worker_count, AttemptEventProgress, Counts, EventRateWindow, Progress,
        ProgressWriter, RecentUnit, TickerPlan, GENERIC_STRUCTURE_ALGORITHM_VERSION,
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
        assert!(retryable_error(
            "error sending request for url (http://clickhouse/?database=q_live)"
        ));
        assert!(retryable_error(
            "ClickHouse idempotent checkpoint request failed before a confirmed response after 5 attempts: connection closed"
        ));
        assert!(!retryable_error("checkpoint algorithm version mismatch"));
    }

    #[test]
    fn systemic_query_contract_failures_abort_the_campaign() {
        assert!(campaign_fatal_error(
            "ClickHouse Code: 386 DB::Exception NO_COMMON_TYPE"
        ));
        assert!(campaign_fatal_error("UNKNOWN_IDENTIFIER source_date"));
        assert!(campaign_fatal_error(
            "refusing to persist a checkpoint whose serialized payload hash drifted"
        ));
        assert!(campaign_fatal_error(
            "daily checkpoint source revision is not historical-sip-condition-v1"
        ));
        assert!(!campaign_fatal_error(
            "ordinal stream authority mismatch for SUGP 2026-08-21"
        ));
    }

    #[test]
    fn daily_structure_certification_accepts_only_the_historical_sip_condition_policy() {
        assert!(validate_daily_structure_source_revision(
            true,
            true,
            "plan-sha256:abc",
            "1:2:0:updated:Archive:1:2:split-sha256:abc:structure-input-v1:archive-sip-condition:trade-condition-sha256:def",
        )
        .is_ok());
        assert_eq!(
            validate_daily_structure_source_revision(
                true,
                true,
                "plan-sha256:abc",
                "1:2:0:updated:Archive:1:2:split-sha256:abc:execution-clock-v1:5:99:0:updated",
            )
            .unwrap_err(),
            "daily checkpoint source revision is not historical-sip-condition-v1"
        );
        assert_eq!(
            validate_daily_structure_source_revision(
                false,
                true,
                "plan-sha256:abc",
                "structure-input-v1:archive-sip-condition:trade-condition-sha256:def",
            )
            .unwrap_err(),
            "daily checkpoint authority is incomplete"
        );
    }

    #[test]
    fn campaign_accepts_up_to_eighty_workers() {
        assert!(validate_worker_count(1).is_ok());
        assert!(validate_worker_count(64).is_ok());
        assert!(validate_worker_count(80).is_ok());
        assert_eq!(
            validate_worker_count(81).unwrap_err(),
            "--workers must be between 1 and 80"
        );
    }

    #[test]
    fn adaptive_ordinal_chunks_are_bounded_and_react_to_fetch_time() {
        assert_eq!(next_ordinal_chunk(250_000, 1_500), 500_000);
        assert_eq!(next_ordinal_chunk(250_000, 6_000), 125_000);
        assert_eq!(next_ordinal_chunk(100_000, 30_000), 100_000);
        assert_eq!(next_ordinal_chunk(1_000_000, 1), 1_000_000);
    }

    #[test]
    fn checkpoint_set_id_is_explicit_and_safe() {
        assert!(validate_checkpoint_set_id("canonical-tradable-20250101-20260831-v16").is_ok());
        assert!(validate_checkpoint_set_id("canonical set").is_err());
        assert!(validate_checkpoint_set_id("x';DROP").is_err());
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
            None,
            "test-set".to_string(),
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
            schema_version: 6,
            status: "running".to_string(),
            started_at: now - chrono::Duration::minutes(5),
            updated_at: now,
            algorithm_version: GENERIC_STRUCTURE_ALGORITHM_VERSION,
            checkpoint_set_id: "test-set".to_string(),
            start_date: NaiveDate::from_ymd_opt(2026, 8, 21).unwrap(),
            end_date: NaiveDate::from_ymd_opt(2026, 8, 31).unwrap(),
            ticker_count: 13_888,
            total_units: 201_694,
            total_estimated_events: 50_000_000,
            counts: Counts {
                active: 2,
                certified: 10_000,
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
            None,
            "test-set".to_string(),
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

    #[test]
    fn campaign_control_is_scoped_to_the_checkpoint_set() {
        let date = NaiveDate::from_ymd_opt(2026, 8, 21).unwrap();
        let root =
            std::env::temp_dir().join(format!("structure-campaign-control-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let control = root.join("campaign-control.json");
        let writer = ProgressWriter::new(
            root.join("status.json"),
            Some(control.clone()),
            "test-set".to_string(),
            date,
            date,
            &[],
        );
        std::fs::write(
            &control,
            br#"{"schema_version":1,"checkpoint_set_id":"other-set","action":"stop_fast"}"#,
        )
        .unwrap();
        assert_eq!(writer.requested_stop_mode(), None);
        std::fs::write(
            &control,
            br#"{"schema_version":1,"checkpoint_set_id":"test-set","action":"stop_fast"}"#,
        )
        .unwrap();
        assert_eq!(writer.requested_stop_mode(), Some(super::StopMode::Fast));
        let _ = std::fs::remove_dir_all(root);
    }
}
