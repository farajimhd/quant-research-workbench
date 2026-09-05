//! Local baseline/candidate comparison: complete canonical days, no database writes.
use chrono::{Duration, NaiveDate, TimeZone, Utc};
use chrono_tz::America::New_York;
use qmd_core::{
    config::load_env_files,
    event::MarketEvent,
    generic_structure::{GenericStructureEngine, GENERIC_STRUCTURE_ALGORITHM_VERSION},
    structure_certification::checkpoint_sha256,
};
use qmd_history_gateway::{config::HistoricalGatewayConfig, source::HistoricalEventSource};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{path::PathBuf, time::Instant};

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() -> Result<(), String> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() != 4 {
        return Err("usage: structure_optimization_probe TICKER START_DATE END_DATE OUTPUT_DIRECTORY (inclusive dates, maximum 7 days / 2 million events / 15 minutes)".into());
    }
    if GENERIC_STRUCTURE_ALGORITHM_VERSION != 18 {
        return Err("requires algorithm 18".into());
    }
    let ticker = args[0].to_uppercase();
    let parse = |s: &str| NaiveDate::parse_from_str(s, "%Y-%m-%d").map_err(|e| e.to_string());
    let start = parse(&args[1])?;
    let end = parse(&args[2])?;
    if end < start || (end - start).num_days() >= 7 {
        return Err("require 1..7 inclusive days".into());
    }
    let output = PathBuf::from(&args[3])
        .canonicalize()
        .map_err(|e| e.to_string())?;
    let root = PathBuf::from(r"D:\TradingML\runtimes")
        .canonicalize()
        .map_err(|e| e.to_string())?;
    if !output.starts_with(root) {
        return Err("output must be under runtime root".into());
    }
    let report_path = output.join(format!("{ticker}.json"));
    if report_path.exists() {
        return Err("refusing to overwrite a completed comparison case".into());
    }
    load_env_files();
    let source = HistoricalEventSource::initialize(HistoricalGatewayConfig::from_env()).await?;
    let dates: Vec<_> = (0..=(end - start).num_days())
        .map(|i| start + Duration::days(i))
        .collect();
    let authority_start = New_York
        .from_local_datetime(&start.and_hms_opt(4, 0, 0).unwrap())
        .single()
        .ok_or("ambiguous start")?
        .with_timezone(&Utc);
    let manifest = source
        .structure_campaign_manifest(&ticker, authority_start, &dates)
        .await?;
    let total: u64 = manifest.sessions.iter().map(|s| s.event_count).sum();
    if total == 0 || total > 2_000_000 {
        return Err(format!("event budget violated: {total}"));
    }
    let rules = source.trade_aggregation_rules();
    let mut engine = GenericStructureEngine::new(&ticker);
    let mut days = Vec::new();
    let mut boundaries = Vec::new();
    let mut input_hash = Sha256::new();
    let mut emission_hash = Sha256::new();
    let mut events = 0_u64;
    let mut apply_seconds = 0.0;
    let timer = Instant::now();
    for session in &manifest.sessions {
        if !session.source_revision.request_complete
            || !session.source_revision.complete_for_history
        {
            return Err("incomplete canonical day".into());
        }
        let day_end = New_York
            .from_local_datetime(&session.session_date.and_hms_opt(20, 0, 0).unwrap())
            .single()
            .ok_or("ambiguous end")?
            .with_timezone(&Utc);
        // Exact production campaign schedule (engine de-duplicates earlier splits).
        for split in manifest
            .split_adjustments
            .iter()
            .filter(|s| s.effective_at <= day_end - Duration::microseconds(1))
        {
            engine.apply_split_adjustment(split)?;
        }
        let mut stream = source.stream_structure_ordinal_range(
            session.session_date,
            &ticker,
            session.first_ordinal,
            session.next_ordinal,
            5000,
        )?;
        let mut day_events = 0;
        let mut previous = None;
        while let Some(batch) =
            tokio::time::timeout(std::time::Duration::from_secs(30), stream.recv())
                .await
                .map_err(|_| "source fetch deadline")?
        {
            for row in batch? {
                if timer.elapsed().as_secs() > 900 || output.join("STOP").exists() {
                    return Err("stopped or exceeded 15 minute limit".into());
                }
                let event = source.market_event(&row);
                let cursor = (event.ts(), event.arrival_sequence());
                if row.ticker.to_uppercase() != ticker
                    || row.arrival_sequence != session.first_ordinal + day_events
                    || event.ts().with_timezone(&New_York).date_naive() != session.session_date
                    || previous.is_some_and(|p| cursor <= p)
                {
                    return Err("canonical identity/order/ordinal mismatch".into());
                }
                previous = Some(cursor);
                let conditions = match &event {
                    MarketEvent::Trade(t) => &t.conditions,
                    MarketEvent::Quote(q) => &q.conditions,
                };
                let rule = rules.resolve(conditions, event.ts());
                input_hash.update(serde_json::to_vec(&event).map_err(|e| e.to_string())?);
                let clock = Instant::now();
                let emitted = engine.apply_event_without_snapshot(&event, rule);
                apply_seconds += clock.elapsed().as_secs_f64();
                emission_hash.update(serde_json::to_vec(&emitted).map_err(|e| e.to_string())?);
                events += 1;
                day_events += 1;
                if events % 5000 == 0 {
                    boundaries.push(json!({"events":events,"checkpoint_sha256":checkpoint_sha256(&engine.checkpoint())?}));
                }
            }
            eprintln!(
                "{ticker} {}: {events}/{total} events, {:.1}s elapsed",
                session.session_date,
                timer.elapsed().as_secs_f64()
            );
        }
        if day_events != session.event_count {
            return Err("source exhausted before full day".into());
        }
        let checkpoint = engine.checkpoint();
        let hash = checkpoint_sha256(&checkpoint)?;
        let bytes = serde_json::to_vec(&checkpoint).map_err(|e| e.to_string())?;
        if bytes.len() > 256 * 1024 * 1024 {
            return Err("checkpoint exceeds 256 MiB".into());
        }
        std::fs::write(
            output.join(format!("{ticker}-{}.checkpoint.json", session.session_date)),
            &bytes,
        )
        .map_err(|e| e.to_string())?;
        let decoded = qmd_core::structure_checkpoint_json::decode_checkpoint(
            std::str::from_utf8(&bytes).map_err(|e| e.to_string())?,
        )?;
        let mut restored = GenericStructureEngine::new(&ticker);
        restored.seed_checkpoint(&decoded);
        if checkpoint_sha256(&restored.checkpoint())? != hash {
            return Err("daily checkpoint restore parity failed".into());
        }
        days.push(json!({"date":session.session_date,"events":day_events,"checkpoint_sha256":hash,"counts":engine.diagnostic_state_counts(),"book":engine.snapshot(day_end-Duration::microseconds(1)),"source_revision":session.source_revision}));
        engine = restored;
    }
    let after = source
        .structure_campaign_manifest(&ticker, authority_start, &dates)
        .await?;
    if manifest.sessions.len() != after.sessions.len()
        || manifest.sessions.iter().zip(&after.sessions).any(|(a, b)| {
            a.session_date != b.session_date
                || a.first_ordinal != b.first_ordinal
                || a.next_ordinal != b.next_ordinal
                || a.source_revision.token != b.source_revision.token
        })
    {
        return Err("source manifest changed during replay".into());
    }
    let report = json!({"ticker":ticker,"start":start,"end":end,"algorithm_version":GENERIC_STRUCTURE_ALGORITHM_VERSION,"events":events,"input_sha256":format!("{:x}",input_hash.finalize()),"emissions_sha256":format!("{:x}",emission_hash.finalize()),"boundaries":boundaries,"days":days,"apply_seconds":apply_seconds,"elapsed_seconds":timer.elapsed().as_secs_f64(),"status":"completed","scope":"fresh construction over requested complete days; no production seed or writes"});
    std::fs::write(
        report_path,
        serde_json::to_vec_pretty(&report).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}
