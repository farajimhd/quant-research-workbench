//! QMD owns the persistent Python V7/MLE engine. Both gateways use this bridge.
//! No browser or backend process owns authoritative level-book state.
use axum::{http::StatusCode, Json};
use serde_json::{json, Value};
use serde::Deserialize;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::{Mutex, OnceLock};

struct Worker { child: Child, input: ChildStdin, output: BufReader<ChildStdout> }
impl Drop for Worker {
    fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); }
}
impl Worker {
    fn start() -> Result<Self, String> {
        let root = std::env::var_os("QMD_LEVEL_BOOK_V7_CODE_ROOT").map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."));
        let root = root.canonicalize().map_err(|e| format!("V7 code root unavailable: {e}"))?;
        let python = std::env::var_os("QMD_LEVEL_BOOK_V7_PYTHON").map(PathBuf::from).unwrap_or_else(|| {
            PathBuf::from(std::env::var_os("USERPROFILE").unwrap_or_default()).join("miniconda3/envs/ml4t/python.exe")
        });
        let mut command = Command::new(python);
        command.arg("-B").arg(root.join("scripts/qmd_level_book_v7_worker.py"))
            .current_dir(&root).env("PYTHONDONTWRITEBYTECODE", "1")
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::inherit());
        #[cfg(windows)] {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command.spawn().map_err(|e| format!("V7 worker unavailable: {e}"))?;
        let input = child.stdin.take().ok_or("V7 stdin unavailable")?;
        let output = BufReader::new(child.stdout.take().ok_or("V7 stdout unavailable")?);
        Ok(Self { child, input, output })
    }
    fn request(&mut self, request: &Value) -> Result<Value, String> {
        serde_json::to_writer(&mut self.input, request).map_err(|e| e.to_string())?;
        self.input.write_all(b"\n").and_then(|_| self.input.flush()).map_err(|e| e.to_string())?;
        let mut line = String::new();
        if self.output.read_line(&mut line).map_err(|e| e.to_string())? == 0 { return Err("V7 worker closed its stream".into()); }
        let response: Value = serde_json::from_str(&line).map_err(|e| format!("V7 protocol error: {e}"))?;
        Ok(response)
    }
}
// A ticker always has one owning process. Parallel ticker preparation never
// shares mutable MLE state or changes the ordering within a cursor.
const WORKER_COUNT: usize = 4;
static WORKERS: OnceLock<Vec<Mutex<Option<Worker>>>> = OnceLock::new();
static ADMISSION: tokio::sync::Semaphore = tokio::sync::Semaphore::const_new(8);
fn worker_index(request: &Value) -> usize {
    request["ticker"].as_str().unwrap_or("").bytes()
        .fold(0usize, |hash, byte| hash.wrapping_mul(31).wrapping_add(byte as usize)) % WORKER_COUNT
}

#[cfg(test)]
mod worker_tests {
    use super::*;
    #[test]
    fn ticker_ownership_is_stable_across_modes_and_operations() {
        let mut owners = std::collections::HashSet::new();
        for ticker in ["AAA", "BBB", "CCC", "DDD"] {
            let owner = worker_index(&json!({"ticker":ticker,"operation":"snapshot","mode":"history"}));
            assert_eq!(owner, worker_index(&json!({"ticker":ticker,"operation":"chart_checkpoint","mode":"live"})));
            owners.insert(owner);
        }
        assert_eq!(owners.len(), WORKER_COUNT);
        assert_eq!(worker_index(&json!({"operation":"catalog"})), 0);
    }
}
pub fn shutdown() {
    if let Some(slots)=WORKERS.get() {
        for slot in slots {
            if let Ok(mut worker)=slot.lock() {
                if let Some(running)=worker.as_mut() {
                    let _=running.request(&json!({"operation":"shutdown"}));
                }
                *worker=None;
            }
        }
    }
}
pub async fn dispatch(request: Value) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let permit = ADMISSION.try_acquire().map_err(|_| (StatusCode::TOO_MANY_REQUESTS, Json(json!({"error":"V7 request queue is full; retry"}))))?;
    let result = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        let slots = WORKERS.get_or_init(|| (0..WORKER_COUNT).map(|_| Mutex::new(None)).collect());
        let mut slot = slots[worker_index(&request)].lock().map_err(|_| "V7 worker lock poisoned".to_string())?;
        if slot.is_none() { *slot = Some(Worker::start()?); }
        let result = slot.as_mut().unwrap().request(&request);
        if result.is_err() { *slot = None; }
        result
    }).await.map_err(|e| (StatusCode::SERVICE_UNAVAILABLE, Json(json!({"error": e.to_string()}))))?;
    let response=result.map_err(|e| (StatusCode::SERVICE_UNAVAILABLE, Json(json!({"error":e}))))?;
    if response["ok"] != true {
        return Err((StatusCode::SERVICE_UNAVAILABLE, Json(json!({"error":response["error"]}))));
    }
    Ok(Json(response["result"].clone()))
}
pub async fn catalog() -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    dispatch(json!({"operation":"catalog"})).await
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CoverageRequest {
    tickers: Vec<String>,
    as_of: chrono::DateTime<chrono::Utc>,
}
pub async fn coverage(Json(request): Json<CoverageRequest>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    if request.tickers.is_empty() || request.tickers.len() > 128 || request.tickers.iter().any(|t|
        t.is_empty() || t.len() > 30 || !t.chars().all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || ".- ".contains(c))) {
        return Err((StatusCode::BAD_REQUEST, Json(json!({"error":"Invalid V7 coverage tickers"}))));
    }
    dispatch(json!({"operation":"coverage","tickers":request.tickers,"as_of":request.as_of})).await
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SnapshotRequest {
    ticker: String,
    as_of: chrono::DateTime<chrono::Utc>,
    #[serde(default)]
    include_segments: bool,
    #[serde(default)]
    cursor_id: String,
    #[serde(default)]
    delta: bool,
    #[serde(default)]
    base_version: Option<String>,
}
async fn snapshot(request: SnapshotRequest, mode: &str) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    if request.cursor_id.len()>64 || request.ticker.is_empty() || request.ticker.len()>30 || !request.ticker.chars().all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || ".- ".contains(c)) {
        return Err((StatusCode::BAD_REQUEST,Json(json!({"error":"Invalid V7 ticker"}))));
    }
    dispatch(json!({"operation":"snapshot","mode":mode,"ticker":request.ticker,"as_of":request.as_of,"include_segments":request.include_segments,"cursor_id":request.cursor_id,"delta":request.delta,"base_version":request.base_version})).await
}
pub async fn history_snapshot(Json(request): Json<SnapshotRequest>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    snapshot(request,"history").await
}
pub async fn live_snapshot(Json(request): Json<SnapshotRequest>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    snapshot(request,"live").await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartCheckpointRequest { ticker: String, as_of: chrono::DateTime<chrono::Utc> }
async fn chart_checkpoint(request: ChartCheckpointRequest, mode: &str) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    if request.ticker.is_empty() || request.ticker.len()>30 || !request.ticker.chars().all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || ".- ".contains(c)) {
        return Err((StatusCode::BAD_REQUEST,Json(json!({"error":"Invalid V7 ticker"}))));
    }
    dispatch(json!({"operation":"chart_checkpoint","mode":mode,"ticker":request.ticker,"as_of":request.as_of})).await
}
pub async fn history_checkpoint(Json(request): Json<ChartCheckpointRequest>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    chart_checkpoint(request,"history").await
}
pub async fn live_checkpoint(Json(request): Json<ChartCheckpointRequest>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    chart_checkpoint(request,"live").await
}
