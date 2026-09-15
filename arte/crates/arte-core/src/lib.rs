//! Network-free shared domain contracts for ARTE Live and historical execution.
#![forbid(unsafe_code)]

pub mod acquisition;
pub mod candidate_runtime;
pub mod config;
pub mod coverage;
pub mod decision_orders;
pub mod event_storage;
pub mod events;
pub mod execution_events;
pub mod execution_positions;
pub mod exposure;
pub mod journal;
pub mod latency;
pub mod market;
pub mod order_funding;
pub mod orders;
pub mod portfolio;
pub mod publication;
pub mod replay;
pub mod seed_storage;
pub mod simulated_execution;
pub mod strategy_adds;
pub mod strategy_candidate;
pub mod strategy_dispatch;
pub mod strategy_early_stop;
pub mod strategy_encounters;
pub mod strategy_entry;
pub mod strategy_lifecycle;
pub mod strategy_management;
pub mod strategy_protection;
pub mod strategy_setup;
pub mod strategy_targets;
pub mod strategy_transaction;
pub mod v7_band;
pub mod v7_encounters;
pub mod v7_evidence;
pub mod v7_extraction;
pub mod v7_fit;
pub mod v7_math;
pub mod v7_peaks;
pub mod v7_seed;
pub mod v7_stream;

use serde::Serialize;
use sha2::{Digest, Sha256};

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum Error {
    #[error("invalid input: {0}")]
    Invalid(String),
    #[error("required state unavailable: {0}")]
    Unready(String),
    #[error("capacity exhausted: {0}")]
    Capacity(String),
    #[error("conflicting immutable identity: {0}")]
    Conflict(String),
    #[error("serialization failed: {0}")]
    Serialization(String),
}
pub type Result<T> = std::result::Result<T, Error>;

/// Stable for the typed versioned structs used here; maps must use BTreeMap.
pub fn content_hash<T: Serialize>(value: &T) -> Result<String> {
    let bytes = serde_json::to_vec(value).map_err(|e| Error::Serialization(e.to_string()))?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}
