//! Read-only, bounded continuation from a certified production checkpoint.
use chrono::{DateTime, Duration as CDuration, NaiveDate, TimeZone, Utc};
use chrono_tz::America::New_York;
use qmd_core::{
    config::{load_env_files, GatewayConfig},
    event::MarketEvent,
    generic_structure::{GenericStructureEngine, GENERIC_STRUCTURE_ALGORITHM_VERSION},
    indicators::IndicatorClickHouseWriter,
    metrics::SharedMetrics,
    structure_certification::{checkpoint_sha256, validate_checkpoint_certification},
};
use qmd_history_gateway::{config::HistoricalGatewayConfig, source::HistoricalEventSource};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};

#[derive(Default, serde::Serialize)]
struct Phase {
    calls: u64,
    milliseconds: f64,
}
#[derive(serde::Serialize)]
struct Progress {
    phase: String,
    events: u64,
    last_event_utc: Option<DateTime<Utc>>,
    phases: BTreeMap<String, Phase>,
    elapsed_seconds: f64,
    active_phase_seconds: f64,
    #[serde(skip)]
    phase_started: Instant,
}
fn phase(progress: &Arc<Mutex<Progress>>, name: &str) {
    let mut p = progress.lock().unwrap();
    p.phase = name.into();
    p.phase_started = Instant::now();
}

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() -> Result<(), String> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() != 6 {
        return Err("usage: structure_checkpoint_replay_probe TICKER TARGET_DATE SET_ID OUTPUT_JSON MAX_EVENTS MAX_SECONDS".into());
    }
    let output = PathBuf::from(&args[3]);
    let root = PathBuf::from(r"D:\TradingML\runtimes")
        .canonicalize()
        .map_err(|e| e.to_string())?;
    let parent = output
        .parent()
        .ok_or("missing output parent")?
        .canonicalize()
        .map_err(|e| e.to_string())?;
    if !parent.starts_with(root) {
        return Err("output must be under runtime root".into());
    }
    let progress = Arc::new(Mutex::new(Progress {
        phase: "starting".into(),
        events: 0,
        last_event_utc: None,
        phases: BTreeMap::new(),
        elapsed_seconds: 0.0,
        active_phase_seconds: 0.0,
        phase_started: Instant::now(),
    }));
    let done = Arc::new(AtomicBool::new(false));
    let clock = Instant::now();
    let status = output.with_extension("status.json");
    let watcher = {
        let p = progress.clone();
        let done = done.clone();
        std::thread::spawn(move || {
            while !done.load(Ordering::Relaxed) {
                let value = {
                    let mut p = p.lock().unwrap();
                    p.elapsed_seconds = clock.elapsed().as_secs_f64();
                    p.active_phase_seconds = p.phase_started.elapsed().as_secs_f64();
                    serde_json::to_vec(&*p).unwrap()
                };
                let temporary = status.with_extension("tmp");
                if let Err(error) = std::fs::write(&temporary, &value)
                    .and_then(|_| std::fs::rename(&temporary, &status))
                {
                    eprintln!("status publication failed: {error}");
                }
                std::thread::sleep(Duration::from_secs(2));
            }
        })
    };
    let result = run(&args, &output, &progress, clock).await;
    done.store(true, Ordering::Relaxed);
    watcher.join().map_err(|_| "status writer failed")?;
    let report = match &result {
        Ok(report) => report.clone(),
        Err(error) => {
            json!({"status":"failed","error":error,"progress":&*progress.lock().unwrap()})
        }
    };
    std::fs::write(&output, serde_json::to_vec_pretty(&report).unwrap())
        .map_err(|e| e.to_string())?;
    result.map(|_| ())
}

async fn run(
    args: &[String],
    output: &PathBuf,
    progress: &Arc<Mutex<Progress>>,
    clock: Instant,
) -> Result<Value, String> {
    let ticker = args[0].to_uppercase();
    let date = NaiveDate::parse_from_str(&args[1], "%Y-%m-%d").map_err(|e| e.to_string())?;
    let max_events: u64 = args[4].parse().map_err(|_| "invalid event budget")?;
    let seconds: u64 = args[5].parse().map_err(|_| "invalid time budget")?;
    if max_events == 0 || max_events > 100_000 || seconds == 0 || seconds > 600 {
        return Err("budgets must be 1..100000 events and 1..600 seconds".into());
    }
    load_env_files();
    let mut config = GatewayConfig::from_env();
    config.structure_checkpoint_set_id = args[2].clone();
    let reader = IndicatorClickHouseWriter::new(config, SharedMetrics::new());
    phase(progress, "validate_storage");
    reader.validate_campaign_schema().await?;
    phase(progress, "load_certified_seed");
    let seed = reader
        .load_daily_structure_checkpoint_before(&ticker, date)
        .await?
        .ok_or("no prior checkpoint in requested set")?;
    if seed.checkpoint_set_id != args[2]
        || seed.algorithm_version != 18
        || GENERIC_STRUCTURE_ALGORITHM_VERSION != 18
        || !seed.source_complete
        || Some(seed.session_date) != date.pred_opt()
    {
        return Err("seed identity, algorithm or completeness mismatch".into());
    }
    phase(progress, "validate_seed_certification");
    let cert = seed
        .certification
        .as_ref()
        .ok_or("seed lacks certification")?;
    validate_checkpoint_certification(
        cert,
        &seed.checkpoint,
        seed.session_date,
        seed.authority_start,
        &seed.source_plan_hash,
        &seed.source_revision_token,
    )?;
    let end = New_York
        .from_local_datetime(&date.and_hms_opt(20, 0, 0).unwrap())
        .single()
        .ok_or("ambiguous session boundary")?
        .with_timezone(&Utc);
    phase(progress, "load_source_revision");
    let source = HistoricalEventSource::initialize(HistoricalGatewayConfig::from_env()).await?;
    let manifest = source
        .structure_campaign_manifest(&ticker, seed.authority_start, &[date])
        .await?;
    let session = manifest.sessions.first().ok_or("missing target session")?;
    let revision = &session.source_revision;
    if !revision.request_complete || !revision.complete_for_history {
        return Err("canonical source coverage incomplete".into());
    }
    let rules = source.trade_aggregation_rules();
    phase(progress, "seed_engines");
    let mut engine = GenericStructureEngine::new(&ticker);
    engine.seed_checkpoint(&seed.checkpoint);
    let mut reference = GenericStructureEngine::new(&ticker);
    reference.seed_checkpoint(&seed.checkpoint);
    if checkpoint_sha256(&engine.checkpoint())? != cert.checkpoint_sha256 {
        return Err("seed restore hash mismatch".into());
    }
    let before = engine.diagnostic_state_counts();
    // Match the campaign's exact daily split schedule and ordinal source range.
    for adjustment in manifest
        .split_adjustments
        .iter()
        .filter(|a| a.effective_at <= end - CDuration::microseconds(1))
    {
        phase(progress, "split_adjustment");
        engine.apply_split_adjustment(adjustment)?;
        reference.apply_split_adjustment(adjustment)?;
    }
    let requested_next = session
        .first_ordinal
        .saturating_add(max_events)
        .min(session.next_ordinal);
    let mut batches = source.stream_structure_ordinal_range(
        date,
        &ticker,
        session.first_ordinal,
        requested_next,
        256,
    )?;
    let stop = output.with_extension("stop");
    let mut events = 0;
    let mut profiled_ms = 0.0;
    let mut reference_ms = 0.0;
    let mut fetch_ms = 0.0;
    let mut previous_cursor = None;
    let mut outcome = "source_exhausted";
    'stream: loop {
        if stop.exists() {
            outcome = "stopped";
            break;
        }
        if clock.elapsed().as_secs() >= seconds {
            outcome = "time_budget";
            break;
        }
        phase(progress, "fetch_events");
        let fetch = Instant::now();
        let batch = tokio::select! {
            batch=batches.recv()=>batch,
            _=tokio::time::sleep(Duration::from_secs(1))=>{fetch_ms+=fetch.elapsed().as_secs_f64()*1000.0;continue;}
        };
        fetch_ms += fetch.elapsed().as_secs_f64() * 1000.0;
        let Some(batch) = batch else {
            if events != requested_next - session.first_ordinal {
                return Err("bounded ordinal stream exhausted early".into());
            }
            if requested_next < session.next_ordinal {
                outcome = "event_budget";
            }
            break;
        };
        for row in batch? {
            if events >= max_events {
                outcome = "event_budget";
                break 'stream;
            }
            if stop.exists() {
                outcome = "stopped";
                break 'stream;
            }
            if clock.elapsed().as_secs() >= seconds {
                outcome = "time_budget";
                break 'stream;
            }
            let event = source.market_event(&row);
            let cursor = (event.ts(), event.arrival_sequence());
            if row.ticker.to_uppercase() != ticker
                || event.ts().with_timezone(&New_York).date_naive() != date
                || row.arrival_sequence != session.first_ordinal + events
                || previous_cursor.is_some_and(|previous| cursor <= previous)
            {
                return Err("canonical event identity, window or order mismatch".into());
            }
            previous_cursor = Some(cursor);
            let conditions = match &event {
                MarketEvent::Trade(e) => &e.conditions,
                MarketEvent::Quote(e) => &e.conditions,
            };
            let rule = rules.resolve(conditions, event.ts());
            let mut stage_start = Instant::now();
            let timer = Instant::now();
            let emitted = engine.apply_event_profiled(&event, rule, &mut |name, enter| {
                let mut p = progress.lock().unwrap();
                if enter {
                    p.phase = name.into();
                    stage_start = Instant::now();
                    p.phase_started = stage_start;
                } else {
                    let phase = p.phases.entry(name.into()).or_default();
                    phase.calls += 1;
                    phase.milliseconds += stage_start.elapsed().as_secs_f64() * 1000.0;
                }
            });
            profiled_ms += timer.elapsed().as_secs_f64() * 1000.0;
            phase(progress, "reference_apply");
            let timer = Instant::now();
            let expected = reference.apply_event_without_snapshot(&event, rule);
            reference_ms += timer.elapsed().as_secs_f64() * 1000.0;
            if serde_json::to_value(emitted).map_err(|e| e.to_string())?
                != serde_json::to_value(expected).map_err(|e| e.to_string())?
            {
                return Err(format!("emitted event parity failed at event {events}"));
            }
            events += 1;
            let mut p = progress.lock().unwrap();
            p.events = events;
            p.last_event_utc = Some(event.ts());
        }
    }
    drop(batches);
    if outcome == "source_exhausted" && events != session.event_count {
        return Err("stream exhausted without covering pinned event count".into());
    }
    phase(progress, "validate_prefix_parity");
    let hash = checkpoint_sha256(&engine.checkpoint())?;
    if hash != checkpoint_sha256(&reference.checkpoint())? {
        return Err("profiled/reference checkpoint parity failed".into());
    }
    phase(progress, "recheck_source_revision");
    let after = source
        .structure_campaign_manifest(&ticker, seed.authority_start, &[date])
        .await?;
    if after
        .sessions
        .first()
        .ok_or("missing rechecked session")?
        .source_revision
        .token
        != revision.token
    {
        return Err("source changed during probe".into());
    }
    phase(progress, "finished");
    Ok(
        json!({"status":"completed","stop_reason":outcome,"ticker":ticker,"target_date":date,"seed_date":seed.session_date,"seed_sha256":cert.checkpoint_sha256,"source_revision":revision.token,"available_events":session.event_count,"processed_events":events,"profiled_apply_ms":profiled_ms,"reference_apply_ms":reference_ms,"fetch_ms":fetch_ms,"elapsed_seconds":clock.elapsed().as_secs_f64(),"checkpoint_sha256":hash,"prefix_parity":true,"initial_state_counts":before,"final_state_counts":engine.diagnostic_state_counts(),"progress":&*progress.lock().unwrap(),"scope":"read-only bounded prefix; no production checkpoint written"}),
    )
}
