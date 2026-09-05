//! Bounded, offline CPU probe over a captured certified ClickHouse checkpoint.
//! No network access or database writes. JSON output goes to stdout.
use qmd_core::{
    generic_structure::{GenericStructureEngine, GENERIC_STRUCTURE_ALGORITHM_VERSION},
    structure_certification::checkpoint_sha256,
    structure_checkpoint_json::decode_checkpoint,
};
use serde_json::json;
use std::{path::PathBuf, time::Instant};

fn main() -> Result<(), String> {
    let path = PathBuf::from(
        std::env::args()
            .nth(1)
            .ok_or("provide captured checkpoint JSON path")?,
    );
    let root = PathBuf::from(r"D:\TradingML\runtimes")
        .canonicalize()
        .map_err(|e| e.to_string())?;
    let path = path.canonicalize().map_err(|e| e.to_string())?;
    if !path.starts_with(root)
        || path.metadata().map_err(|e| e.to_string())?.len() > 512 * 1024 * 1024
    {
        return Err("input must be a bounded runtime capture".into());
    }
    let row: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&path).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
    println!("{}", measure(&row)?);
    Ok(())
}

fn measure(row: &serde_json::Value) -> Result<serde_json::Value, String> {
    let snapshot = row["snapshot_json"]
        .as_str()
        .ok_or("missing snapshot_json")?;
    let cert: serde_json::Value = serde_json::from_str(
        row["certification_json"]
            .as_str()
            .ok_or("missing certification")?,
    )
    .map_err(|e| e.to_string())?;
    let start = Instant::now();
    let checkpoint = decode_checkpoint(snapshot)?;
    let decode_ms = start.elapsed().as_secs_f64() * 1000.;
    if checkpoint.algorithm_version != GENERIC_STRUCTURE_ALGORITHM_VERSION {
        return Err("algorithm mismatch".into());
    }
    let start = Instant::now();
    let hash = checkpoint_sha256(&checkpoint)?;
    let hash_ms = start.elapsed().as_secs_f64() * 1000.;
    if cert["checkpoint_sha256"].as_str() != Some(hash.as_str()) {
        return Err("captured certification hash mismatch".into());
    }
    let start = Instant::now();
    let encoded = serde_json::to_string(&checkpoint).map_err(|e| e.to_string())?;
    let encode_ms = start.elapsed().as_secs_f64() * 1000.;
    let start = Instant::now();
    let clone = checkpoint.clone();
    let clone_ms = start.elapsed().as_secs_f64() * 1000.;
    let mut engine = GenericStructureEngine::new(&checkpoint.sym);
    let start = Instant::now();
    engine.seed_checkpoint(&clone);
    let seed_ms = start.elapsed().as_secs_f64() * 1000.;
    let start = Instant::now();
    let restored = engine.checkpoint();
    let extract_ms = start.elapsed().as_secs_f64() * 1000.;
    let restored_hash = checkpoint_sha256(&restored)?;
    let roundtrip_hash = checkpoint_sha256(&decode_checkpoint(&encoded)?)?;
    if hash != restored_hash || hash != roundtrip_hash {
        return Err("checkpoint roundtrip parity failed".into());
    }
    Ok(
        json!({"ticker":checkpoint.sym,"algorithm_version":checkpoint.algorithm_version,"session_date":row["session_date"],"checkpoint_sha256":hash,"snapshot_bytes":snapshot.len(),"decode_ms":decode_ms,"hash_ms":hash_ms,"encode_ms":encode_ms,"clone_ms":clone_ms,"seed_ms":seed_ms,"extract_ms":extract_ms,"parity":true}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn synthetic_checkpoint_roundtrip_and_corruption_detection() {
        let checkpoint = GenericStructureEngine::new("TEST").checkpoint();
        let hash = checkpoint_sha256(&checkpoint).unwrap();
        let mut row = json!({"snapshot_json":serde_json::to_string(&checkpoint).unwrap(),
            "certification_json":json!({"checkpoint_sha256":hash}).to_string(), "session_date":"2026-08-21"});
        assert_eq!(measure(&row).unwrap()["parity"], true);
        row["certification_json"] = json!("{\"checkpoint_sha256\":\"wrong\"}");
        assert!(measure(&row).unwrap_err().contains("hash mismatch"));
    }
}
