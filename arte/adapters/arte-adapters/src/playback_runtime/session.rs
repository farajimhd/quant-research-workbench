//! Fresh single-instrument session assembly. No I/O or broker capability.
//! Recovery uses the common-cut restore path, never this constructor.
use super::{candidates::Candidates, simulation_runtime, Runtime};
use arte_core::{
    candidate_config::Config,
    execution_positions::Projection,
    market_structure::scheduler::playback::{accounts::Run, Mode},
    portfolio::{Account, Portfolio},
    run_manifest::Pinned,
    simulated_execution::Simulator,
    simulation_costs, simulation_model, Error, Result,
};
use std::collections::{BTreeMap, BTreeSet};
pub mod document;

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Limits {
    pub maximum_orders: usize,
    pub maximum_positions: usize,
    pub maximum_fills: usize,
    pub maximum_lots_per_position: usize,
    pub maximum_pending_fills: usize,
    pub maximum_candidate_state_bytes: usize,
}
pub struct Request<'a> {
    pub manifest: &'a Pinned,
    pub configurations: BTreeMap<String, Config>,
    pub accounts: BTreeMap<String, Account>,
    pub price_scale: u8,
    pub fill_model: simulation_model::Model,
    pub cost_model: simulation_costs::Model,
    pub limits: Limits,
}
pub struct Session {
    pub controller: Runtime,
    pub candidates: Candidates,
    pub portfolio: Portfolio,
    startup_hash: String,
}
impl Session {
    /// Require a separately supplied startup identity before constructing owners.
    pub fn from_document(
        run: Run,
        manifest: &Pinned,
        document: document::Document,
        expected_hash: &str,
    ) -> Result<Self> {
        let hash = document.hash()?;
        if hash != expected_hash || document.manifest_hash != manifest.hash() {
            return Err(Error::Conflict("backtest startup identity differs".into()));
        }
        Self::assemble(run, document.into_request(manifest), hash)
    }
    pub fn startup_hash(&self) -> &str {
        &self.startup_hash
    }
    /// All source, feature, quote, fill and cost pins are checked by their
    /// authorities. Account budgets remain explicit simulation inputs, not
    /// evidence of broker cash or certified settlement currency.
    #[cfg(test)]
    pub(crate) fn new(run: Run, request: Request<'_>) -> Result<Self> {
        let startup_hash = document::Document::from_request(&request).hash()?;
        Self::assemble(run, request, startup_hash)
    }
    fn assemble(run: Run, request: Request<'_>, startup_hash: String) -> Result<Self> {
        let manifest = request.manifest;
        if run.manifest_hash() != manifest.hash() || run.status().mode != Mode::Paused {
            return Err(Error::Conflict(
                "fresh session requires matching paused run".into(),
            ));
        }
        let instrument = run.market()?.source_scope().instrument;
        if manifest
            .manifest()
            .consumers
            .iter()
            .any(|c| c.instrument != instrument)
        {
            return Err(Error::Conflict(
                "session requires a single instrument manifest".into(),
            ));
        }
        let expected: BTreeSet<_> = run.scopes().iter().map(|s| s.account.as_str()).collect();
        if !expected
            .iter()
            .copied()
            .eq(request.accounts.keys().map(String::as_str))
        {
            return Err(Error::Conflict(
                "session account set differs from consumers".into(),
            ));
        }
        if request.limits.maximum_positions < expected.len() {
            return Err(Error::Capacity(
                "session position capacity below account count".into(),
            ));
        }
        for account in request.accounts.values() {
            if account.simulation_run_id.as_deref() != Some(manifest.manifest().run_id.as_str())
                || !account.reservations.is_empty()
                || account.currency != request.cost_model.currency
                || account.currency_scale != request.cost_model.currency_scale
            {
                return Err(Error::Conflict(
                    "fresh simulation account provenance or currency".into(),
                ));
            }
        }
        let costs = simulation_costs::Pinned::new(request.cost_model, manifest)?;
        let simulator = Simulator::new_scoped(
            &manifest.manifest().run_id,
            instrument,
            request.price_scale,
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
        let controller = Runtime::new(run, execution, request.fill_model, costs)?;
        let candidates = Candidates::configured(
            &controller,
            manifest,
            request.configurations,
            request.limits.maximum_candidate_state_bytes,
        )?;
        let portfolio = Portfolio::new(request.accounts)?;
        Ok(Self {
            controller,
            candidates,
            portfolio,
            startup_hash,
        })
    }
}
