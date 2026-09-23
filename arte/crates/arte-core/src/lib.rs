//! Network-free shared domain contracts for ARTE Live and historical execution.
#![forbid(unsafe_code)]

pub mod account_boundary;
pub mod acquisition;
pub mod action_rejection;
pub mod bar_catalogue;
pub mod bar_tape;
pub mod boolean_catalogue;
pub mod boolean_compute;
pub mod candidate_config;
pub mod candidate_features;
pub mod candidate_runtime;
pub mod config;
pub mod coverage;
pub mod decision_orders;
pub mod dependency_plan;
pub mod event_boolean;
pub mod event_order;
pub mod event_storage;
pub mod events;
pub mod exact_bars;
pub mod execution_events;
pub mod execution_interval;
pub mod execution_positions;
pub mod exposure;
pub mod journal;
pub mod latency;
pub mod local_swings;
pub mod luld;
pub mod market;
pub mod market_structure;
pub mod order_funding;
pub mod orders;
pub mod portfolio;
pub mod publication;
pub mod quote_state;
pub mod reference_data;
pub mod replay;
pub mod run_manifest;
pub mod seed_storage;
pub mod session;
pub mod simulated_execution;
pub mod simulation_costs;
pub mod simulation_model;
pub mod strategy350_bar_screen;
pub mod strategy350_bos;
pub mod strategy350_catalogue;
pub mod strategy350_effective;
pub mod strategy350_gap;
pub mod strategy350_macd;
pub mod strategy350_noise;
pub mod strategy350_price_gate;
pub mod strategy350_screen_join;
pub mod strategy350_session;
pub mod strategy350_signal;
pub mod strategy350_transaction;
pub mod strategy_adds;
pub mod strategy_candidate;
pub mod strategy_dispatch;
pub mod strategy_early_stop;
pub mod strategy_encounters;
pub mod strategy_entry;
pub mod strategy_lifecycle;
pub mod strategy_macd;
pub mod strategy_management;
pub mod strategy_protection;
pub mod strategy_setup;
pub mod strategy_targets;
pub mod strategy_transaction;
pub mod structure_projection;
pub mod trade_eligibility;
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
