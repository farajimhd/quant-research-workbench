//! Fresh Strategy 350 multi-ticker assembly with one shared account portfolio.
//! The run's market shards are constructed from pinned source and seed catalogs.
//! No broker capability, I/O, or implicit recovery is available here.
use super::{simulation_runtime, Limits};
use crate::playback_runtime::{multi::MultiRuntime, strategy350_accounts::Accounts, Runtime};
use arte_core::{
    content_hash,
    execution_positions::Projection,
    market_structure::scheduler::playback::{accounts::Run, sources::Catalog, Mode},
    portfolio::{Account, Portfolio},
    run_manifest::{Clock, Pinned},
    simulated_execution::Simulator,
    simulation_costs, simulation_model,
    strategy350_effective::Config,
    strategy_dispatch::{Mode as TradingMode, StrategyKind},
    Error, Result,
};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

pub struct MarketRun {
    pub run: Run,
    pub price_scale: u8,
}
pub struct Request<'a, S> {
    pub manifest: &'a Pinned,
    pub sources: &'a Catalog,
    pub markets: Vec<MarketRun>,
    pub configurations: BTreeMap<String, Config>,
    pub initial_states: BTreeMap<String, S>,
    pub accounts: BTreeMap<String, Account>,
    pub fill_model: simulation_model::Model,
    pub cost_model: simulation_costs::Model,
    pub limits: Limits,
}
pub struct Session<S> {
    pub controller: MultiRuntime,
    pub strategy: BTreeMap<u64, Accounts<S>>,
    pub portfolio: Portfolio,
    startup_hash: String,
}
impl<S: Clone + Serialize> Request<'_, S> {
    pub fn hash(&self) -> Result<String> {
        if self.markets.is_empty() || self.markets.len() > 100_000 {
            return Err(Error::Capacity("multi-session market shard budget".into()));
        }
        let mut markets = self
            .markets
            .iter()
            .map(|market| {
                let scope = market.run.market_scope();
                Ok((
                    scope.provider,
                    scope.instrument,
                    scope.session,
                    market.price_scale,
                    market.run.prepared_hash().to_owned(),
                    market.run.market()?.configuration_hash().to_owned(),
                    market.run.quotes()?.policy_hash()?.to_owned(),
                ))
            })
            .collect::<Result<Vec<_>>>()?;
        markets.sort_by_key(|row| (row.0, row.1, row.2));
        content_hash(&(
            "arte.strategy350-multi-session-startup.v1",
            self.manifest.hash(),
            self.sources.hash()?,
            markets,
            &self.configurations,
            &self.initial_states,
            &self.accounts,
            self.fill_model.hash()?,
            self.cost_model.hash()?,
            &self.limits,
        ))
    }
}
impl<S: crate::playback_runtime::strategy350_accounts::StateContract> Session<S> {
    /// The expected startup hash must be pinned independently by the run
    /// launcher. Initial balances are hypothetical per-account inputs.
    pub fn from_request(mut request: Request<'_, S>, expected_hash: &str) -> Result<Self> {
        let startup_hash = request.hash()?;
        if startup_hash != expected_hash
            || request.sources.hash()? != request.manifest.manifest().source_manifest_hash
            || request.manifest.manifest().clock != Clock::Historical
            || request.manifest.manifest().mode != TradingMode::Backtest
            || request
                .manifest
                .manifest()
                .consumers
                .iter()
                .any(|consumer| consumer.strategy_kind != StrategyKind::Strategy350)
        {
            return Err(Error::Conflict(
                "Strategy 350 multi-session run identity".into(),
            ));
        }
        let expected_accounts = request
            .manifest
            .manifest()
            .consumers
            .iter()
            .map(|consumer| consumer.account.as_str())
            .collect::<BTreeSet<_>>();
        if !expected_accounts
            .iter()
            .copied()
            .eq(request.accounts.keys().map(String::as_str))
        {
            return Err(Error::Conflict("multi-session account set differs".into()));
        }
        if request.accounts.values().any(|account| {
            account.simulation_run_id.as_deref()
                != Some(request.manifest.manifest().run_id.as_str())
                || !account.reservations.is_empty()
                || account.currency != request.cost_model.currency
                || account.currency_scale != request.cost_model.currency_scale
        }) {
            return Err(Error::Conflict(
                "multi-session account provenance or currency".into(),
            ));
        }
        let portfolio = Portfolio::new(request.accounts)?;
        let costs = simulation_costs::Pinned::new(request.cost_model, request.manifest)?;
        let mut controllers = Vec::with_capacity(request.markets.len());
        let mut strategy = BTreeMap::new();
        for market in request.markets {
            let run = market.run;
            let scope = run.market_scope();
            let instrument = scope.instrument;
            let status = run.status();
            if run.manifest_hash() != request.manifest.hash()
                || status.mode != Mode::Paused
                || status.completed_frames != 0
                || status.admitted_events != 0
                || status.coalesced_events != 0
                || status.queued_events != 0
                || status.acknowledged_boundaries != 0
                || status.pending_boundary
                || strategy.contains_key(&instrument)
            {
                return Err(Error::Conflict(
                    "multi-session market is not fresh or unique".into(),
                ));
            }
            let local = run
                .scopes()
                .iter()
                .map(content_hash)
                .collect::<Result<Vec<_>>>()?;
            if request.limits.maximum_positions < local.len() {
                return Err(Error::Capacity(
                    "multi-session position budget below accounts".into(),
                ));
            }
            let mut configs = BTreeMap::new();
            let mut states = BTreeMap::new();
            for key in local {
                let config = request.configurations.remove(&key).ok_or_else(|| {
                    Error::Unready("multi-session Strategy 350 config missing".into())
                })?;
                let state = request.initial_states.remove(&key).ok_or_else(|| {
                    Error::Unready("multi-session Strategy 350 state missing".into())
                })?;
                configs.insert(key.clone(), config);
                states.insert(key, state);
            }
            let owner = Accounts::new(
                request.manifest,
                instrument,
                configs,
                states,
                request.limits.maximum_candidate_state_bytes,
            )?;
            let simulator = Simulator::new_scoped(
                &request.manifest.manifest().run_id,
                instrument,
                market.price_scale,
                request.limits.maximum_orders,
                request.fill_model.participation_bps,
            )?;
            let projection = Projection::new(
                request.limits.maximum_positions,
                request.limits.maximum_fills,
                request.limits.maximum_lots_per_position,
            )?;
            let mut execution = simulation_runtime::Runtime::new(
                simulator,
                projection,
                request.limits.maximum_pending_fills,
            )?;
            execution.bind_source(run.market()?.source_scope())?;
            let mut controller = Runtime::new(
                run,
                execution,
                request.fill_model.clone(),
                simulation_costs::Pinned::new(costs.model().clone(), request.manifest)?,
            )?;
            controller.startup_hash = Some(startup_hash.clone());
            strategy.insert(instrument, owner);
            controllers.push(controller);
        }
        if !request.configurations.is_empty() || !request.initial_states.is_empty() {
            return Err(Error::Conflict(
                "multi-session surplus strategy scope input".into(),
            ));
        }
        let controller = MultiRuntime::new(request.manifest, request.sources, controllers)?;
        Ok(Self {
            controller,
            strategy,
            portfolio,
            startup_hash,
        })
    }
    pub fn startup_hash(&self) -> &str {
        &self.startup_hash
    }
}
