//! External integration implementations. Constructors do not connect or start services.
#![forbid(unsafe_code)]
pub mod calendar;
pub mod clickhouse;
pub mod event_writer;
pub mod fill_journal;
pub mod ibkr;
pub mod live_decode;
pub mod live_market;
pub mod live_pipeline;
pub mod maintenance;
pub mod maintenance_pool;
pub mod maintenance_runtime;
pub mod massive;
pub mod massive_stream;
pub mod order_journal;
pub mod ownership;
pub mod request_governor;
pub mod rest_acquisition;
pub mod simulation_runtime;
pub mod startup_references;
pub mod startup_repair;
pub mod startup_sources;
pub mod stoppable_fetcher;
#[cfg(test)]
fn test_quote_policy() -> arte_core::quote_state::eligibility::Pinned {
    use arte_core::quote_state::eligibility::{Pinned, Policy};
    let policy = Policy {
        provider: 1,
        valid_from_ns: 0,
        valid_to_ns: u64::MAX,
        available_at_ns: 0,
        source_manifest_hash: "a".repeat(64),
        allowed_conditions: Default::default(),
        allowed_indicators: Default::default(),
        allow_empty_conditions: true,
        allow_empty_indicators: true,
    };
    let hash = arte_core::content_hash(&policy).unwrap();
    Pinned::new(policy, &hash).unwrap()
}
pub mod strategy_journal;
