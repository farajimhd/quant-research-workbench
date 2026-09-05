//! Read-only bounded validation. Never writes a production checkpoint or runs orders.
use chrono::{DateTime, Utc};
use qmd_core::{
    config::load_env_files,
    event::MarketEvent,
    generic_structure::{
        GenericStructureCheckpoint, GenericStructureEngine, GENERIC_STRUCTURE_ALGORITHM_VERSION,
    },
};
use qmd_history_gateway::{
    config::HistoricalGatewayConfig,
    source::{EventWindow, HistoricalEventSource},
};
use serde_json::json;
use std::{path::PathBuf, time::Instant};

#[tokio::main]
async fn main() -> Result<(), String> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() < 4 {
        return Err("usage: structure_prominence_probe TICKER START_UTC END_UTC OUTPUT_JSON [SNAPSHOT_UTC ...]; max 2 million events, 256 MiB serialized checkpoint, 15 minutes".into());
    }
    let parse = |s: &str| {
        DateTime::parse_from_rfc3339(s)
            .map(|v| v.with_timezone(&Utc))
            .map_err(|e| e.to_string())
    };
    let ticker = args[0].to_uppercase();
    let start = parse(&args[1])?;
    let end = parse(&args[2])?;
    if start >= end || end - start > chrono::Duration::days(7) {
        return Err("Require a positive window of at most seven days".into());
    }
    let output = PathBuf::from(&args[3]);
    let root = PathBuf::from(r"D:\TradingML\runtimes")
        .canonicalize()
        .map_err(|e| e.to_string())?;
    let parent = output
        .parent()
        .ok_or("output parent missing")?
        .canonicalize()
        .map_err(|e| e.to_string())?;
    if !parent.starts_with(&root) {
        return Err("Output must be under D:\\TradingML\\runtimes".into());
    }
    let mut cuts = args[4..]
        .iter()
        .map(|s| parse(s))
        .collect::<Result<Vec<_>, _>>()?;
    cuts.push(end);
    cuts.sort();
    cuts.dedup();
    if cuts.iter().any(|cut| *cut < start || *cut > end) {
        return Err("Snapshots must be within the requested window".into());
    }
    load_env_files();
    let source = HistoricalEventSource::initialize(HistoricalGatewayConfig::from_env()).await?;
    let window = EventWindow {
        start,
        end,
        tickers: vec![ticker.clone()],
    };
    let revision = source.structure_source_revision(&window).await?;
    if !revision.request_complete {
        return Err("Canonical source coverage incomplete".into());
    }
    let rules = source.trade_aggregation_rules();
    let splits = source
        .structure_split_adjustments(&ticker, start, end)
        .await?;
    let mut next_split = 0;
    let mut batches = source.stream_structure_ordered_filtered(
        window.clone(),
        5_000,
        revision.live_continuation_sequence,
        None,
        revision.event_count,
    )?;
    let mut engine = GenericStructureEngine::new(&ticker);
    let mut restored: Option<GenericStructureEngine> = None;
    let mut count = 0_u64;
    let mut snapshots = Vec::new();
    let mut next_cut = 0;
    let timer = Instant::now();
    let mut checkpoint_bytes = 0;
    while let Some(batch) = batches.recv().await {
        for row in batch? {
            if count >= 2_000_000 || timer.elapsed().as_secs() > 900 {
                return Err("Probe resource budget exceeded; no events silently skipped".into());
            }
            let event = source.market_event(&row);
            while next_cut < cuts.len() && cuts[next_cut] < event.ts() {
                snapshots.push(json!({"as_of":cuts[next_cut],"book":engine.snapshot(cuts[next_cut]),"construction_audit":engine.construction_audit()}));
                next_cut += 1;
            }
            while next_split < splits.len() && splits[next_split].effective_at <= event.ts() {
                engine.apply_split_adjustment(&splits[next_split])?;
                if let Some(other) = &mut restored {
                    other.apply_split_adjustment(&splits[next_split])?;
                }
                next_split += 1;
            }
            let conditions = match &event {
                MarketEvent::Trade(e) => &e.conditions,
                MarketEvent::Quote(e) => &e.conditions,
            };
            let rule = rules.resolve(conditions, event.ts());
            engine.apply_event_without_snapshot(&event, rule);
            if let Some(other) = &mut restored {
                other.apply_event_without_snapshot(&event, rule);
            }
            count += 1;
        }
        let bytes = serde_json::to_vec(&engine.checkpoint()).map_err(|e| e.to_string())?;
        checkpoint_bytes = bytes.len();
        if checkpoint_bytes > 256 * 1024 * 1024 {
            return Err("Checkpoint exceeded 256 MiB budget; no levels discarded".into());
        }
        if restored.is_none() {
            let checkpoint: GenericStructureCheckpoint =
                serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
            let mut other = GenericStructureEngine::new(&ticker);
            other.seed_checkpoint(&checkpoint);
            restored = Some(other);
        }
        eprintln!(
            "{ticker}: {count} events, {:.1}s, checkpoint {checkpoint_bytes} bytes",
            timer.elapsed().as_secs_f64()
        );
    }
    while next_split < splits.len() {
        engine.apply_split_adjustment(&splits[next_split])?;
        if let Some(other) = &mut restored {
            other.apply_split_adjustment(&splits[next_split])?;
        }
        next_split += 1;
    }
    while next_cut < cuts.len() {
        snapshots.push(json!({"as_of":cuts[next_cut],"book":engine.snapshot(cuts[next_cut]),"construction_audit":engine.construction_audit()}));
        next_cut += 1;
    }
    let continuous = serde_json::to_value(engine.snapshot(end)).map_err(|e| e.to_string())?;
    let resumed = restored
        .map(|other| serde_json::to_value(other.snapshot(end)))
        .transpose()
        .map_err(|e| e.to_string())?;
    let parity = resumed.as_ref() == Some(&continuous);
    let after = source.structure_source_revision(&window).await?;
    if revision.token != after.token {
        return Err("Canonical source changed during probe".into());
    }
    let result = json!({"algorithm_version":GENERIC_STRUCTURE_ALGORITHM_VERSION,"ticker":ticker,"start":start,"end":end,"processed_events":count,"elapsed_seconds":timer.elapsed().as_secs_f64(),"checkpoint_bytes":checkpoint_bytes,"checkpoint_resume_parity":parity,"source_revision":revision,"snapshots":snapshots,"historical_warmup":"fresh construction from specified start; not a complete multi-month production book"});
    std::fs::write(
        output,
        serde_json::to_vec_pretty(&result).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    if !parity {
        return Err("Checkpoint resume parity failed; inspect output".into());
    }
    Ok(())
}
