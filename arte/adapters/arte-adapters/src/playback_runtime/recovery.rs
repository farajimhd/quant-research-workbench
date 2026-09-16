//! Common-cut recovery for a single-instrument, multi-account backtest.
//! This owns no persistence transport and never advances input during restore.
use super::{
    candidates::{self, Candidates},
    checkpoint, simulation_runtime, Runtime,
};
use arte_core::{
    candidate_config::Config,
    execution_events::Fill,
    journal::Record,
    market_structure::scheduler::{
        checkpoint::Request,
        playback::{sources::Catalog, Prepared},
    },
    portfolio::{
        checkpoint::{Cut, Limits as PortfolioLimits},
        Portfolio,
    },
    run_manifest::Pinned,
    seed_storage::Object,
    simulation_costs::{Pinned as Costs, SettlementCurrency},
    strategy_transaction::Committed,
    Error, Result,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Root {
    version: u32,
    manifest: String,
    cut: Cut,
    controller: String,
    candidates: String,
    portfolio: String,
}
pub struct Bundle {
    pub root: Object,
    pub controller: checkpoint::Bundle,
    pub candidates: candidates::checkpoint::Bundle,
    pub portfolio: Object,
}
pub struct Recovered {
    pub controller: Runtime,
    pub candidates: Candidates,
    pub portfolio: Portfolio,
}
pub struct Limits {
    pub maximum_bytes: usize,
    pub execution: simulation_runtime::checkpoint::Limits,
    pub portfolio: PortfolioLimits,
    pub maximum_state_bytes: usize,
}
fn require(manifest: &Pinned, limits: &Limits) -> Result<()> {
    let consumers = &manifest.manifest().consumers;
    if limits.maximum_bytes == 0
        || limits.maximum_bytes > 64 * 1024 * 1024
        || consumers.is_empty()
        || consumers
            .iter()
            .any(|c| c.instrument != consumers[0].instrument)
    {
        return Err(Error::Invalid(
            "recovery requires one instrument and a bounded graph".into(),
        ));
    }
    Ok(())
}
fn controller_objects(bundle: &checkpoint::Bundle) -> impl Iterator<Item = &Object> {
    let scheduler = &bundle.playback.playback.scheduler;
    [
        &bundle.root,
        &bundle.execution.root,
        &bundle.playback.root,
        &bundle.playback.playback.root,
        &scheduler.root,
        &scheduler.market,
        &scheduler.trades,
        &scheduler.quotes,
        &scheduler.book,
    ]
    .into_iter()
    .chain(bundle.playback.barrier.iter())
    .chain(bundle.execution.objects.values())
}
fn remaining<'a>(maximum: usize, mut objects: impl Iterator<Item = &'a Object>) -> Result<usize> {
    objects
        .try_fold(maximum, |left, object| {
            left.checked_sub(object.payload.len())
        })
        .ok_or_else(|| Error::Capacity("run recovery total bytes".into()))
}
impl Bundle {
    /// Exclusive references hold all three owners at a synchronous cut. No
    /// network publication occurs here; callers must publish the root last.
    #[allow(clippy::too_many_arguments)]
    pub fn capture(
        controller: &mut Runtime,
        candidates: &mut Candidates,
        portfolio: &mut Portfolio,
        manifest: &Pinned,
        cut: &Cut,
        last_fills: &BTreeMap<String, Fill>,
        currencies: &BTreeMap<u64, SettlementCurrency>,
        limits: &Limits,
    ) -> Result<Self> {
        require(manifest, limits)?;
        controller.execution.require_complete_portfolio(
            portfolio,
            manifest
                .manifest()
                .consumers
                .iter()
                .map(|c| c.account.clone()),
            currencies,
        )?;
        let controller_image = controller.checkpoint(
            manifest,
            cut,
            last_fills,
            limits.execution,
            limits.maximum_bytes,
        )?;
        let left = remaining(limits.maximum_bytes, controller_objects(&controller_image))?;
        let candidates_image = candidates.checkpoint(controller, left)?;
        let left = remaining(
            left,
            [&candidates_image.root, &candidates_image.features]
                .into_iter()
                .chain(candidates_image.candidates.values()),
        )?;
        let portfolio_limits = PortfolioLimits {
            maximum_bytes: limits.portfolio.maximum_bytes.min(left),
            maximum_accounts: limits.portfolio.maximum_accounts,
            maximum_reservations: limits.portfolio.maximum_reservations,
            maximum_settlements: limits.portfolio.maximum_settlements,
        };
        let portfolio_image = portfolio.checkpoint(manifest, cut, &portfolio_limits)?;
        let root = Object::new(
            serde_json::to_vec(&Root {
                version: 1,
                manifest: manifest.hash().into(),
                cut: cut.clone(),
                controller: controller_image.root.id.clone(),
                candidates: candidates_image.root.id.clone(),
                portfolio: portfolio_image.id.clone(),
            })
            .map_err(|e| Error::Serialization(e.to_string()))?,
        );
        let bundle = Self {
            root,
            controller: controller_image,
            candidates: candidates_image,
            portfolio: portfolio_image,
        };
        bundle.require_size(limits.maximum_bytes)?;
        Ok(bundle)
    }
    fn require_size(&self, maximum: usize) -> Result<()> {
        remaining(
            maximum,
            [
                &self.root,
                &self.portfolio,
                &self.candidates.root,
                &self.candidates.features,
            ]
            .into_iter()
            .chain(controller_objects(&self.controller))
            .chain(self.candidates.candidates.values()),
        )?;
        Ok(())
    }
    /// The expected root is an independently trusted publication pin. Receipts
    /// must be recovered from durable journals, never manufactured from hashes.
    /// Component restore independently checks the supplied journal readbacks.
    #[allow(clippy::too_many_arguments)]
    pub fn restore(
        &self,
        expected_root: &str,
        manifest: &Pinned,
        cut: &Cut,
        sources: &Catalog,
        prepared: Prepared,
        market_request: Request<'_>,
        frames_per_poll: usize,
        maximum_consumers: usize,
        receipts: &[&Committed],
        costs: Costs,
        configurations: BTreeMap<String, Config>,
        readbacks: &BTreeMap<String, Vec<Record>>,
        currencies: &BTreeMap<u64, SettlementCurrency>,
        limits: &Limits,
    ) -> Result<Recovered> {
        require(manifest, limits)?;
        self.require_size(limits.maximum_bytes)?;
        if self.root.id != expected_root {
            return Err(Error::Conflict("run recovery root pin".into()));
        }
        self.root.verify()?;
        let root: Root = serde_json::from_slice(&self.root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if root.version != 1
            || root.manifest != manifest.hash()
            || root.cut != *cut
            || root.controller != self.controller.root.id
            || root.candidates != self.candidates.root.id
            || root.portfolio != self.portfolio.id
            || serde_json::to_vec(&root).map_err(|e| Error::Serialization(e.to_string()))?
                != self.root.payload
        {
            return Err(Error::Conflict("run recovery component pins differ".into()));
        }
        let mut portfolio = Portfolio::restore_checkpoint(
            manifest,
            cut,
            &self.portfolio,
            &root.portfolio,
            &limits.portfolio,
        )?;
        let controller = Runtime::restore_checkpoint(
            &self.controller,
            &root.controller,
            manifest,
            cut,
            sources,
            prepared,
            market_request,
            frames_per_poll,
            maximum_consumers,
            receipts,
            costs,
            limits.execution,
            limits.maximum_bytes,
        )?;
        controller.execution.require_complete_portfolio(
            &mut portfolio,
            manifest
                .manifest()
                .consumers
                .iter()
                .map(|c| c.account.clone()),
            currencies,
        )?;
        let candidates = Candidates::restore_checkpoint(
            &self.candidates,
            &root.candidates,
            &controller,
            configurations,
            readbacks,
            limits.maximum_state_bytes,
            limits.maximum_bytes,
        )?;
        Ok(Recovered {
            controller,
            candidates,
            portfolio,
        })
    }
}
