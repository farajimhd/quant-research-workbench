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
pub mod playback_runtime;
pub mod rejection_journal;
pub mod replay_sources;
pub mod request_governor;
pub mod rest_acquisition;
pub mod simulation_runtime;
pub mod startup_quote_policies;
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
#[cfg(test)]
fn test_fill_model() -> arte_core::simulation_model::Model {
    arte_core::simulation_model::Model {
        schema_version: 1,
        algorithm: arte_core::simulated_execution::MODEL.into(),
        participation_bps: 10000,
        submission_latency_ns: 0,
        maximum_quote_age_ns: 2_000_000_000,
    }
}
#[cfg(test)]
fn test_simulation_costs(run_id: &str) -> arte_core::simulation_costs::Pinned {
    use arte_core::{
        run_manifest::{Clock, Consumer, Execution, Manifest, Pinned},
        simulation_costs,
        strategy_dispatch::Mode,
    };
    let model = simulation_costs::Model {
        schema_version: 1,
        currency: "USD".into(),
        currency_scale: 2,
        fixed_per_fill_minor: 0,
        per_share_atoms: 0,
        per_share_scale: 0,
        minimum_per_fill_minor: 0,
    };
    let hash = "a".repeat(64);
    let manifest = Manifest {
        schema_version: 1,
        run_id: run_id.into(),
        mode: Mode::Backtest,
        code_release_hash: hash.clone(),
        source_manifest_hash: hash.clone(),
        reference_manifest_hash: hash.clone(),
        seed_manifest_hash: hash.clone(),
        algorithm_manifest_hash: hash.clone(),
        dependency_plan_hash: hash.clone(),
        hardware_profile_hash: hash.clone(),
        clock: Clock::Historical,
        execution: Execution::Simulated {
            fill_model_hash: test_fill_model().hash().unwrap(),
            cost_model_hash: model.hash().unwrap(),
        },
        consumers: ["a", "b"]
            .into_iter()
            .map(|account| Consumer {
                account: account.into(),
                instrument: 1,
                strategy_instance: "s".into(),
                effective_config_hash: hash.clone(),
            })
            .collect(),
    };
    let id = manifest.hash().unwrap();
    simulation_costs::Pinned::new(model, &Pinned::new(manifest, &id).unwrap()).unwrap()
}
