//! Network-free shared domain contracts for ARTE Live and historical execution.
#![forbid(unsafe_code)]

pub mod config;
pub mod coverage;
pub mod events;
pub mod latency;
pub mod market;
pub mod orders;
pub mod portfolio;
pub mod publication;
pub mod replay;
pub mod strategy_setup;
pub mod v7_encounters;
pub mod v7_evidence;
pub mod v7_math;

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
