//! Unpublished multi-ticker image graph. Pins alone never authorize recovery.
use super::*;
use arte_core::{
    market_structure::scheduler::playback::accounts::checkpoint::Bundle as MarketBundle,
    portfolio::checkpoint::{Cut, Limits as PortfolioLimits},
    seed_storage::Object,
};
use serde::{Deserialize, Serialize};

pub struct Limits {
    pub maximum_bytes: usize,
    pub execution: simulation_runtime::checkpoint::Limits,
    pub portfolio: PortfolioLimits,
}

pub struct Bundle {
    pub root: Object,
    pub markets: BTreeMap<u64, MarketBundle>,
    pub strategies: BTreeMap<u64, super::super::strategy350_accounts::checkpoint::Bundle>,
    pub executions: BTreeMap<u64, simulation_runtime::checkpoint::Bundle>,
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
    market: String,
    strategy: String,
    execution: String,
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
        if root.version != 1
            || root.manifest != manifest.hash()
            || root.startup != startup
            || root.cut != *cut
            || root.portfolio != self.portfolio.id
            || !root.shards.contains_key(&root.selected_instrument)
            || root.shards[&root.selected_instrument].head != cut.boundary_hash
            || root.shards[&root.selected_instrument].sequence != cut.boundary_sequence
            || !root.shards.keys().eq(self.markets.keys())
            || !root.shards.keys().eq(self.strategies.keys())
            || !root.shards.keys().eq(self.executions.keys())
            || serde_json::to_vec(&root).map_err(|error| Error::Serialization(error.to_string()))?
                != self.root.payload
        {
            return Err(Error::Conflict("multi-run graph pins differ".into()));
        }
        let mut used = 0usize;
        add_bytes(&mut used, &self.root)?;
        add_bytes(&mut used, &self.portfolio)?;
        for (instrument, pin) in &root.shards {
            let market = &self.markets[instrument];
            let strategy = &self.strategies[instrument];
            let execution = &self.executions[instrument];
            if pin.provider == 0
                || pin.session == 0
                || pin.head.is_empty()
                || pin.sequence == 0
                || pin.market != market.root.id
                || pin.strategy != strategy.root.id
                || pin.execution != execution.root.id
            {
                return Err(Error::Conflict("multi-run shard pins differ".into()));
            }
            add_bytes(&mut used, &market.root)?;
            add_bytes(&mut used, &market.playback.root)?;
            let scheduler = &market.playback.scheduler;
            for object in [
                &scheduler.root,
                &scheduler.market,
                &scheduler.trades,
                &scheduler.quotes,
                &scheduler.book,
            ] {
                add_bytes(&mut used, object)?;
            }
            if let Some(barrier) = &market.barrier {
                add_bytes(&mut used, barrier)?;
            }
            add_bytes(&mut used, &strategy.root)?;
            for object in strategy.accounts.values() {
                add_bytes(&mut used, object)?;
            }
            add_bytes(&mut used, &execution.root)?;
            for object in execution.objects.values() {
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
        let markets = self.capture_market_shards(cut, maximum)?;
        let strategies = self.capture_strategy350_shards(owners, cut, maximum)?;
        let execution_images = self.capture_execution_shards(
            portfolio,
            manifest,
            cut,
            last_fills,
            currencies,
            limits.execution,
            maximum,
        )?;
        let executions = self
            .controllers
            .iter()
            .map(|lane| lane.market_scope().instrument)
            .zip(execution_images)
            .collect::<BTreeMap<_, _>>();
        if executions.len() != self.controllers.len() {
            return Err(Error::Conflict(
                "multi-run execution shard set differs".into(),
            ));
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
                market: markets[&instrument].root.id.clone(),
                strategy: strategies[&instrument].root.id.clone(),
                execution: executions[&instrument].root.id.clone(),
            };
            if shards.insert(instrument, shard).is_some() {
                return Err(Error::Conflict("multi-run duplicate instrument".into()));
            }
        }
        let root = Object::new(
            serde_json::to_vec(&Root {
                version: 1,
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
            markets,
            strategies,
            executions,
            portfolio: portfolio_image,
        };
        graph.pins(manifest, startup, cut, maximum)?;
        Ok(graph)
    }
}
