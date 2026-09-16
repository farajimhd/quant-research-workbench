//! Short-lived, offline transport replay. Input is explicit JSONL requests over
//! certified prepared frames; this starts no HTTP listener or backtest orders.
use qmd_core::level_book_v7 as bridge;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::{BufRead, BufReader, Write};
use std::time::Instant;

#[cfg(windows)]
fn private_bytes() -> usize {
    #[repr(C)]
    #[derive(Default)]
    struct Counters {
        cb: u32, page_faults: u32, peak_working: usize, working: usize,
        peak_paged: usize, paged: usize, peak_nonpaged: usize, nonpaged: usize,
        pagefile: usize, peak_pagefile: usize, private: usize,
    }
    #[link(name = "psapi")]
    extern "system" { fn GetProcessMemoryInfo(process: *mut std::ffi::c_void, counters: *mut Counters, size: u32) -> i32; }
    #[link(name = "kernel32")]
    extern "system" { fn GetCurrentProcess() -> *mut std::ffi::c_void; }
    let mut counters = Counters::default();
    counters.cb = std::mem::size_of::<Counters>() as u32;
    // The API writes this exact PROCESS_MEMORY_COUNTERS_EX layout.
    if unsafe { GetProcessMemoryInfo(GetCurrentProcess(), &mut counters, counters.cb) } == 0 { 0 }
    else { counters.private }
}
#[cfg(not(windows))]
fn private_bytes() -> usize { 0 }

fn allocation_snapshot() -> Value {
    #[cfg(feature = "allocation-diagnostics")]
    { serde_json::to_value(qmd_history_gateway::allocation_diagnostics::snapshot()).unwrap() }
    #[cfg(not(feature = "allocation-diagnostics"))]
    { json!({"enabled": false, "runtime_revision": qmd_history_gateway::EXECUTION_RUNTIME_REVISION}) }
}

#[derive(serde::Deserialize)]
struct PacketVersion { version: String, base_version: Option<String> }
#[derive(serde::Deserialize)]
struct RowVersion {
    ticker: String,
    packet: Option<PacketVersion>,
    seed_packet: Option<PacketVersion>,
    error: Option<Value>,
}
#[derive(serde::Deserialize)]
struct ResponseVersions { rows: Vec<RowVersion> }

async fn replay(path: &str, payload_path: Option<&str>) -> Result<(), String> {
    let reader = BufReader::new(std::fs::File::open(path).map_err(|e| e.to_string())?);
    let mut payload_file = payload_path.map(std::fs::File::create).transpose().map_err(|e| e.to_string())?;
    let mut versions = std::collections::HashMap::<String, String>::new();
    println!("{}", json!({"phase":"start","private_bytes":private_bytes(),"allocations":allocation_snapshot()}));
    for (index, line) in reader.lines().enumerate() {
        let line = line.map_err(|e| e.to_string())?;
        let mut request: Value = serde_json::from_str(&line).map_err(|e| e.to_string())?;
        let operation = request["operation"].as_str().unwrap_or("").to_owned();
        if operation == "advance" {
            let mut seen = std::collections::HashSet::new();
            for row in request["requests"].as_array_mut().ok_or("missing requests")? {
                let ticker = row["ticker"].as_str().ok_or("missing ticker")?.to_owned();
                row["base_version"] = json!(versions.get(&ticker));
                row["continue_batch"] = json!(!seen.insert(ticker));
            }
        }
        let started = Instant::now();
        let response = bridge::prepared_stream(axum::Json(request)).await
            .map_err(|(status, value)| format!("request {index}: {status}: {}", value.0))?;
        // Includes the same serialization Axum performs for the HTTP body.
        let body = serde_json::to_vec(&response.0).map_err(|e| e.to_string())?;
        drop(response);
        let transport_seconds = started.elapsed().as_secs_f64();
        let body_bytes = body.len();
        // Skip nested snapshots rather than rebuilding the object trees under
        // investigation. Semantic comparison runs separately on saved payloads.
        let value: ResponseVersions = serde_json::from_slice(&body).map_err(|e| e.to_string())?;
        if value.rows.iter().any(|row| row.error.is_some()) {
            return Err(format!("request {index} returned a row error"));
        }
        let row_count = value.rows.len();
        for row in value.rows {
            if let Some(packet) = row.packet.or(row.seed_packet) {
                if packet.base_version.as_ref() != versions.get(&row.ticker) { return Err("delta version continuity changed".into()); }
                versions.insert(row.ticker, packet.version);
            }
        }
        let digest = format!("{:x}", Sha256::digest(&body));
        if let Some(file) = payload_file.as_mut() {
            file.write_all(&body).and_then(|_| file.write_all(b"\n")).map_err(|e| e.to_string())?;
        }
        drop(body);
        println!("{}", json!({"phase":"request","index":index,"operation":operation,
            "rows":row_count,"body_bytes":body_bytes,"sha256":digest,
            "transport_seconds":transport_seconds,"private_bytes":private_bytes(),
            "allocations":allocation_snapshot()}));
        if index % 100 == 0 { eprintln!("V7 transport probe: {} requests complete", index + 1); }
        // Fail before this diagnostic can reproduce a machine-wide OOM.
        if private_bytes() > 8 * 1024usize.pow(3) { return Err("probe exceeded its 8 GiB native memory safety limit".into()); }
    }
    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), String> {
    let path = std::env::args().nth(1).ok_or("usage: v7_transport_memory_probe REQUESTS.jsonl [PAYLOADS.jsonl]")?;
    let payload_path = std::env::args().nth(2);
    qmd_core::config::load_env_files();
    let result = replay(&path, payload_path.as_deref()).await;
    bridge::shutdown();
    println!("{}", json!({"phase":"closed","private_bytes":private_bytes(),"allocations":allocation_snapshot(),"ok":result.is_ok()}));
    result
}
