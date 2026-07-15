#![recursion_limit = "512"]

//! Shared QMD market-data contracts and processing engines.
//!
//! The live `qmd-gateway` binary and the historical gateway compile against
//! these exact modules. Keeping the existing live implementation here avoids a
//! copied historical implementation and makes contract drift a compile-time
//! dependency change rather than a manual synchronization task.

pub mod api;
pub mod bars;
pub mod clickhouse;
pub mod compact_event;
pub mod config;
pub mod event;
pub mod flatfile;
pub mod gapfill;
pub mod indicator_catalog;
pub mod indicators;
pub mod intraday_bars;
pub mod live_market_state;
pub mod maintenance;
pub mod market_calendar;
pub mod market_products;
pub mod massive;
pub mod metrics;
pub mod scanner;
pub mod session;
pub mod signal_catalog;
pub mod state;
pub mod timefmt;
