//! Unpublished multi-ticker image graph. Pins alone never authorize recovery.
use super::*;
use arte_core::{
    journal::Record,
    market_structure::scheduler::playback::{sources::Catalog, Prepared},
    portfolio::checkpoint::{Cut, Limits as PortfolioLimits},
    seed_storage::{Bundle as SeedBundle, Object},
    strategy350_effective::Config,
    strategy_transaction::Committed,
};
use serde::{de::DeserializeOwned, Deserialize, Serialize};

pub struct Limits {
    pub maximum_bytes: usize,
    pub execution: simulation_runtime::checkpoint::Limits,
    pub portfolio: PortfolioLimits,
    pub maximum_strategy_state_bytes: usize,
}

pub struct ShardEvidence<'a> {
    pub startup: &'a super::super::session::market::Document,
    pub expected_startup_hash: &'a str,
    pub prepared: Prepared,
    pub seed: &'a SeedBundle,
    pub receipts: Vec<&'a Committed>,
    pub strategy_configurations: BTreeMap<String, Config>,
    pub strategy_readbacks: BTreeMap<String, Vec<Record>>,
}

pub struct Recovered<S> {
    pub controller: MultiRuntime,
    pub strategy: BTreeMap<u64, super::super::strategy350_accounts::Accounts<S>>,
    pub portfolio: Portfolio,
}

pub struct Bundle {
    pub root: Object,
    pub controllers: BTreeMap<u64, super::super::checkpoint::Bundle>,
    pub strategies: BTreeMap<u64, super::super::strategy350_accounts::checkpoint::Bundle>,
    pub portfolio: Object,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Root {
    version: u32,
    manifest: String,
    startup: String,
    cut: Cut,
    selected_instrument: u64,
    shards: BTreeMap<u64, Shard>,
    portfolio: String,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Shard {
    provider: u16,
    session: u32,
    head: String,
    sequence: u64,
    controller: String,
    strategy: String,
}

fn add_bytes(used: &mut usize, object: &Object) -> Result<()> {
    object.verify()?;
    *used = used
        .checked_add(object.payload.len())
        .ok_or_else(|| Error::Capacity("multi-run graph size overflow".into()))?;
    Ok(())
}

impl Bundle {
    fn pins(&self, manifest: &Pinned, startup: &str, cut: &Cut, maximum: usize) -> Result<()> {
        if maximum == 0 || maximum > 64 * 1024 * 1024 {
            return Err(Error::Capacity("multi-run graph byte budget".into()));
        }
        self.root.verify()?;
        let root: Root = serde_json::from_slice(&self.root.payload)
            .map_err(|error| Error::Serialization(error.to_string()))?;
        if root.version != 2
            || root.manifest != manifest.hash()
            || root.startup != startup
            || root.cut != *cut
            || root.portfolio != self.portfolio.id
            || !root.shards.contains_key(&root.selected_instrument)
            || root.shards[&root.selected_instrument].head != cut.boundary_hash
            || root.shards[&root.selected_instrument].sequence != cut.boundary_sequence
            || !root.shards.keys().eq(self.controllers.keys())
            || !root.shards.keys().eq(self.strategies.keys())
            || serde_json::to_vec(&root).map_err(|error| Error::Serialization(error.to_string()))?
                != self.root.payload
        {
            return Err(Error::Conflict("multi-run graph pins differ".into()));
        }
        let mut used = 0usize;
        add_bytes(&mut used, &self.root)?;
        add_bytes(&mut used, &self.portfolio)?;
        for (instrument, pin) in &root.shards {
            let controller = &self.controllers[instrument];
            let strategy = &self.strategies[instrument];
            if pin.provider == 0
                || pin.session == 0
                || pin.head.is_empty()
                || pin.sequence == 0
                || pin.controller != controller.root.id
                || pin.strategy != strategy.root.id
            {
                return Err(Error::Conflict("multi-run shard pins differ".into()));
            }
            add_bytes(&mut used, &controller.root)?;
            add_bytes(&mut used, &controller.playback.root)?;
            add_bytes(&mut used, &controller.playback.playback.root)?;
            let scheduler = &controller.playback.playback.scheduler;
            for object in [
                &scheduler.root,
                &scheduler.market,
                &scheduler.trades,
                &scheduler.quotes,
                &scheduler.book,
            ] {
                add_bytes(&mut used, object)?;
            }
            if let Some(barrier) = &controller.playback.barrier {
                add_bytes(&mut used, barrier)?;
            }
            add_bytes(&mut used, &strategy.root)?;
            for object in strategy.accounts.values() {
                add_bytes(&mut used, object)?;
            }
            add_bytes(&mut used, &controller.execution.root)?;
            for object in controller.execution.objects.values() {
                add_bytes(&mut used, object)?;
            }
        }
        if used > maximum {
            return Err(Error::Capacity("multi-run graph byte budget".into()));
        }
        Ok(())
    }

    /// Pin and size checks only; this is not semantic restore or durability.
    pub fn verify_pins(
        &self,
        expected_root: &str,
        manifest: &Pinned,
        startup: &str,
        cut: &Cut,
        maximum_bytes: usize,
    ) -> Result<()> {
        if self.root.id != expected_root {
            return Err(Error::Conflict("multi-run expected root differs".into()));
        }
        self.pins(manifest, startup, cut, maximum_bytes)
    }

    /// Offline semantic restore from independently supplied source, startup,
    /// journal and cost evidence. No storage publication or trading authority.
    #[allow(clippy::too_many_arguments)]
    pub fn restore<S: Clone + Serialize + DeserializeOwned>(
        &self,
        expected_root: &str,
        manifest: &Pinned,
        startup: &str,
        cut: &Cut,
        sources: &Catalog,
        seeds: &super::super::session::market::seed_catalog::RunSeedCatalog,
        mut evidence: BTreeMap<u64, ShardEvidence<'_>>,
        cost_model: &arte_core::simulation_costs::Model,
        last_fills: &BTreeMap<usize, BTreeMap<String, Fill>>,
        currencies: &BTreeMap<u64, SettlementCurrency>,
        limits: &Limits,
    ) -> Result<Recovered<S>> {
        self.verify_pins(expected_root, manifest, startup, cut, limits.maximum_bytes)?;
        let root: Root = serde_json::from_slice(&self.root.payload)
            .map_err(|error| Error::Serialization(error.to_string()))?;
        if !root.shards.keys().eq(evidence.keys())
            || sources.hash()? != manifest.manifest().source_manifest_hash
        {
            return Err(Error::Conflict(
                "multi-run independent shard evidence differs".into(),
            ));
        }
        seeds.require_run(manifest, sources)?;
        let mut portfolio = Portfolio::restore_checkpoint(
            manifest,
            cut,
            &self.portfolio,
            &root.portfolio,
            &limits.portfolio,
        )?;
        let mut controllers = Vec::with_capacity(root.shards.len());
        let mut strategies = BTreeMap::new();
        for (instrument, shard) in &root.shards {
            let input = evidence
                .remove(instrument)
                .ok_or_else(|| Error::Conflict("multi-run shard evidence missing".into()))?;
            if input.startup.hash()? != input.expected_startup_hash
                || input.startup.manifest_hash != manifest.hash()
                || input.startup.configuration.provider != shard.provider
                || input.startup.configuration.instrument != *instrument
                || input.startup.configuration.session != shard.session
                || input.prepared.scope().provider != shard.provider
                || input.prepared.scope().instrument != *instrument
                || input.prepared.scope().session != shard.session
                || (*instrument != root.selected_instrument && !input.receipts.is_empty())
            {
                return Err(Error::Conflict(
                    "multi-run shard startup or receipts differ".into(),
                ));
            }
            let start_ns = input
                .startup
                .configuration
                .start_second
                .checked_mul(1_000_000_000)
                .ok_or_else(|| Error::Capacity("multi-run seed start clock".into()))?;
            seeds.require_shard(
                manifest,
                sources,
                arte_core::event_order::Scope {
                    provider: shard.provider,
                    instrument: *instrument,
                    session: shard.session,
                },
                start_ns,
                input.seed,
            )?;
            let seed_hash = input.seed.hydrate()?.hash;
            let context = if *instrument == root.selected_instrument {
                content_hash(&("arte.playback-controller-cut.v1", manifest.hash(), cut))?
            } else {
                content_hash(&(
                    "arte.playback-controller-standby.v1",
                    manifest.hash(),
                    cut,
                    shard.provider,
                    instrument,
                    shard.session,
                    shard.head.as_str(),
                    shard.sequence,
                ))?
            };
            let policy = arte_core::quote_state::eligibility::Pinned::new(
                input.startup.quote_policy.clone(),
                &content_hash(&input.startup.quote_policy)?,
            )?;
            let configuration_hash = input
                .startup
                .configuration
                .recovery_hash(&input.startup.split)?;
            let market_request = arte_core::market_structure::scheduler::checkpoint::Request {
                context_hash: &context,
                run_id: &manifest.manifest().run_id,
                seed_hash: &seed_hash,
                configuration_hash: &configuration_hash,
                quote_policy: std::sync::Arc::new(policy),
                maximum_pending: input.startup.maximum_pending_events,
                maximum_bytes: limits.maximum_bytes,
            };
            let costs = arte_core::simulation_costs::Pinned::new(cost_model.clone(), manifest)?;
            let image = &self.controllers[instrument];
            let mut controller = if *instrument == root.selected_instrument {
                Runtime::restore_checkpoint(
                    image,
                    &shard.controller,
                    manifest,
                    cut,
                    sources,
                    input.prepared,
                    market_request,
                    input.startup.frames_per_poll,
                    input.startup.maximum_consumers,
                    &input.receipts,
                    costs,
                    limits.execution,
                    limits.maximum_bytes,
                )?
            } else {
                Runtime::restore_standby_checkpoint(
                    image,
                    &shard.controller,
                    manifest,
                    cut,
                    sources,
                    input.prepared,
                    market_request,
                    input.startup.frames_per_poll,
                    input.startup.maximum_consumers,
                    costs,
                    limits.execution,
                    limits.maximum_bytes,
                )?
            };
            controller.startup_hash = Some(startup.into());
            let strategy_image = &self.strategies[instrument];
            let strategy = if *instrument == root.selected_instrument {
                super::super::strategy350_accounts::Accounts::<S>::restore_checkpoint(
                    strategy_image,
                    &shard.strategy,
                    &controller,
                    input.strategy_configurations,
                    &input.strategy_readbacks,
                    limits.maximum_strategy_state_bytes,
                    limits.maximum_bytes,
                )?
            } else {
                super::super::strategy350_accounts::Accounts::<S>::restore_standby_checkpoint(
                    strategy_image,
                    &shard.strategy,
                    &controller,
                    cut,
                    input.strategy_configurations,
                    &input.strategy_readbacks,
                    limits.maximum_strategy_state_bytes,
                    limits.maximum_bytes,
                )?
            };
            strategies.insert(*instrument, strategy);
            controllers.push(controller);
        }
        let mut controller = MultiRuntime::new(manifest, sources, controllers)?;
        let selected = controller
            .controllers
            .iter()
            .position(|lane| lane.market_scope().instrument == root.selected_instrument)
            .ok_or_else(|| Error::Conflict("multi-run selected controller missing".into()))?;
        controller.selected = Some((selected, cut.boundary_hash.clone()));
        controller.selected()?;
        controller.require_complete_portfolio(&mut portfolio, manifest, currencies)?;
        let recaptured = controller.capture_strategy350_graph(
            &strategies,
            &mut portfolio,
            manifest,
            startup,
            cut,
            last_fills,
            currencies,
            limits,
        )?;
        if recaptured.root.id != expected_root {
            return Err(Error::Conflict("multi-run restored graph differs".into()));
        }
        Ok(Recovered {
            controller,
            strategy: strategies,
            portfolio,
        })
    }
}

impl MultiRuntime {
    /// Synchronous in-memory capture. Publication, readback, semantic restore
    /// and account reconciliation must precede any recovery acknowledgement.
    #[allow(clippy::too_many_arguments)]
    pub fn capture_strategy350_graph<S: Clone + serde::Serialize>(
        &self,
        owners: &BTreeMap<u64, super::super::strategy350_accounts::Accounts<S>>,
        portfolio: &mut Portfolio,
        manifest: &Pinned,
        startup: &str,
        cut: &Cut,
        last_fills: &BTreeMap<usize, BTreeMap<String, Fill>>,
        currencies: &BTreeMap<u64, SettlementCurrency>,
        limits: &Limits,
    ) -> Result<Bundle> {
        let maximum = limits.maximum_bytes;
        if maximum == 0 || maximum > 64 * 1024 * 1024 || startup.len() != 64 {
            return Err(Error::Capacity("multi-run graph bounds".into()));
        }
        let (selected, _) = self
            .selected()?
            .ok_or_else(|| Error::Unready("multi-run selected boundary absent".into()))?;
        if self.controllers.iter().any(|lane| {
            lane.manifest_hash() != manifest.hash() || lane.startup_hash.as_deref() != Some(startup)
        }) {
            return Err(Error::Conflict("multi-run graph startup differs".into()));
        }
        if last_fills
            .keys()
            .any(|index| *index >= self.controllers.len())
        {
            return Err(Error::Invalid("multi-run fill shard differs".into()));
        }
        self.require_complete_portfolio(portfolio, manifest, currencies)?;
        let strategies = self.capture_strategy350_shards(owners, cut, maximum)?;
        let mut controllers = BTreeMap::new();
        let empty = BTreeMap::new();
        for (index, lane) in self.controllers.iter().enumerate() {
            lane.actions.require_complete()?;
            let fills = last_fills.get(&index).unwrap_or(&empty);
            let image = if index == selected {
                lane.checkpoint(manifest, cut, fills, limits.execution, maximum)?
            } else {
                lane.checkpoint_standby(manifest, cut, fills, limits.execution, maximum)?
            };
            if controllers
                .insert(lane.market_scope().instrument, image)
                .is_some()
            {
                return Err(Error::Conflict("multi-run duplicate controller".into()));
            }
        }
        let portfolio_image =
            self.capture_portfolio(portfolio, manifest, cut, currencies, &limits.portfolio)?;
        let mut shards = BTreeMap::new();
        for lane in &self.controllers {
            let scope = lane.market_scope();
            let head = lane
                .run
                .pending()?
                .ok_or_else(|| Error::Unready("multi-run shard head absent".into()))?;
            let instrument = scope.instrument;
            let shard = Shard {
                provider: scope.provider,
                session: scope.session,
                head: head.id.into(),
                sequence: head.sequence,
                controller: controllers[&instrument].root.id.clone(),
                strategy: strategies[&instrument].root.id.clone(),
            };
            if shards.insert(instrument, shard).is_some() {
                return Err(Error::Conflict("multi-run duplicate instrument".into()));
            }
        }
        let root = Object::new(
            serde_json::to_vec(&Root {
                version: 2,
                manifest: manifest.hash().into(),
                startup: startup.into(),
                cut: cut.clone(),
                selected_instrument: self.controllers[selected].market_scope().instrument,
                shards,
                portfolio: portfolio_image.id.clone(),
            })
            .map_err(|error| Error::Serialization(error.to_string()))?,
        );
        let graph = Bundle {
            root,
            controllers,
            strategies,
            portfolio: portfolio_image,
        };
        graph.pins(manifest, startup, cut, maximum)?;
        Ok(graph)
    }
}
