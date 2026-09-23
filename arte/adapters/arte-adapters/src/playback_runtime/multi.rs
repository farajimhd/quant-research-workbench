//! Cross-ticker control over full playback controllers, including simulated
//! fills, journal gates, actions and checkpoint ownership. No broker capability.
use super::{
    candidates::Candidates,
    recovery::publication::{Finalized, Owners, Published},
    recovery::Limits,
    runner::{Inputs, Journals, Step},
    Runtime,
};
use crate::simulation_runtime;
use arte_core::{
    content_hash,
    execution_events::Fill,
    market_structure::scheduler::playback::{sources::Catalog, Mode, Poll},
    portfolio::Portfolio,
    run_manifest::{Clock, Pinned},
    simulation_costs::SettlementCurrency,
    Error, Result,
};
use std::collections::{BTreeMap, BTreeSet};
#[path = "multi_checkpoint.rs"]
pub mod checkpoint;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MultiPoll {
    Paused,
    Yield,
    NeedsFills { shard: usize, scope_hash: String },
    Boundary { shard: usize },
    Complete,
}

pub struct MultiRuntime {
    controllers: Vec<Runtime>,
    selected: Option<(usize, String)>,
}

type Order = (u64, u64, u16, u64, u32, u64);
fn choose_head<'a>(
    heads: impl IntoIterator<Item = (Order, usize, &'a str)>,
) -> Option<(usize, String)> {
    heads
        .into_iter()
        .min_by_key(|(order, _, _)| *order)
        .map(|(_, index, id)| (index, id.to_owned()))
}

impl MultiRuntime {
    #[cfg(test)]
    pub(crate) fn seed_test_order(
        &mut self,
        shard: usize,
        bracket: arte_core::orders::Bracket,
        at_ns: u64,
    ) -> Result<()> {
        self.controllers
            .get_mut(shard)
            .ok_or_else(|| Error::Invalid("test order shard missing".into()))?
            .seed_test_order(bracket, at_ns)
    }

    pub fn new(manifest: &Pinned, catalog: &Catalog, controllers: Vec<Runtime>) -> Result<Self> {
        if controllers.is_empty()
            || controllers.len() > 100_000
            || controllers.len() != catalog.shards.len()
        {
            return Err(Error::Capacity("multi-controller shard budget".into()));
        }
        if manifest.manifest().clock != Clock::Historical
            || catalog.clock != Clock::Historical
            || catalog.hash()? != manifest.manifest().source_manifest_hash
        {
            return Err(Error::Conflict("multi-controller source pin".into()));
        }
        let mut keyed = controllers
            .into_iter()
            .map(|controller| {
                let scope = controller.market_scope();
                (
                    (scope.provider, scope.instrument, scope.session),
                    controller,
                )
            })
            .collect::<Vec<_>>();
        keyed.sort_by_key(|(key, _)| *key);
        for ((key, controller), shard) in keyed.iter().zip(&catalog.shards) {
            if *key != (shard.provider, shard.instrument, shard.session)
                || controller.prepared_hash() != shard.prepared_hash
                || controller.manifest_hash() != manifest.hash()
                || controller.run_id() != manifest.manifest().run_id
            {
                return Err(Error::Conflict("multi-controller shard set".into()));
            }
        }
        let instruments = catalog
            .shards
            .iter()
            .map(|shard| shard.instrument)
            .collect::<BTreeSet<_>>();
        if manifest
            .manifest()
            .consumers
            .iter()
            .any(|consumer| !instruments.contains(&consumer.instrument))
        {
            return Err(Error::Conflict(
                "multi-controller consumer lacks source".into(),
            ));
        }
        Ok(Self {
            controllers: keyed
                .into_iter()
                .map(|(_, controller)| controller)
                .collect(),
            selected: None,
        })
    }

    pub fn shard_count(&self) -> usize {
        self.controllers.len()
    }

    /// All lanes must agree with one shared portfolio. The exact union must
    /// account for every reservation and settlement; no unsubmitted plan may
    /// hide in an account at a recoverable cut.
    pub fn require_complete_portfolio(
        &self,
        portfolio: &mut Portfolio,
        manifest: &Pinned,
        currencies: &BTreeMap<u64, SettlementCurrency>,
    ) -> Result<()> {
        if self
            .controllers
            .iter()
            .any(|controller| controller.manifest_hash() != manifest.hash())
        {
            return Err(Error::Conflict(
                "multi-controller portfolio run differs".into(),
            ));
        }
        let mut expected: BTreeMap<String, (BTreeSet<String>, BTreeSet<String>)> = manifest
            .manifest()
            .consumers
            .iter()
            .map(|consumer| (consumer.account.clone(), (BTreeSet::new(), BTreeSet::new())))
            .collect();
        for controller in &self.controllers {
            for (account, (reserved, settled)) in controller
                .execution
                .checkpoint_funding(portfolio, currencies)?
            {
                let (all_reserved, all_settled) = expected.get_mut(&account).ok_or_else(|| {
                    Error::Conflict("multi-controller funding account differs".into())
                })?;
                for command in reserved {
                    if !all_reserved.insert(command.clone()) || all_settled.contains(&command) {
                        return Err(Error::Conflict(
                            "multi-controller duplicate funding command".into(),
                        ));
                    }
                }
                for command in settled {
                    if !all_settled.insert(command.clone()) || all_reserved.contains(&command) {
                        return Err(Error::Conflict(
                            "multi-controller duplicate funding command".into(),
                        ));
                    }
                }
            }
        }
        portfolio.require_checkpoint_funding(&expected)
    }

    /// Capture account cash at the selected global boundary only after every
    /// execution lane and the exact funding union pass the shared cut gate.
    pub fn capture_portfolio(
        &self,
        portfolio: &mut Portfolio,
        manifest: &Pinned,
        cut: &arte_core::portfolio::checkpoint::Cut,
        currencies: &BTreeMap<u64, SettlementCurrency>,
        limits: &arte_core::portfolio::checkpoint::Limits,
    ) -> Result<arte_core::seed_storage::Object> {
        let (index, controller) = self
            .selected()?
            .ok_or_else(|| Error::Unready("multi-controller portfolio cut absent".into()))?;
        let boundary = controller
            .decision_view()?
            .pending()?
            .ok_or_else(|| Error::Unready("multi-controller portfolio boundary absent".into()))?;
        if self.selected.as_ref().map(|(shard, _)| *shard) != Some(index)
            || cut.boundary_hash != boundary.id
            || cut.at_ns != boundary.evaluated_at_ns
            || controller.status().acknowledged_boundaries.checked_add(1)
                != Some(cut.boundary_sequence)
        {
            return Err(Error::Conflict(
                "multi-controller portfolio cut differs".into(),
            ));
        }
        for lane in &self.controllers {
            lane.actions.require_complete()?;
        }
        self.require_complete_portfolio(portfolio, manifest, currencies)?;
        portfolio.checkpoint(manifest, cut, limits)
    }

    /// Selected execution uses the global boundary clock. Other lanes retain
    /// their independently validated local frontiers, never a fabricated cut.
    #[allow(clippy::too_many_arguments)]
    pub fn capture_execution_shards(
        &self,
        portfolio: &mut Portfolio,
        manifest: &Pinned,
        cut: &arte_core::portfolio::checkpoint::Cut,
        last_fills: &BTreeMap<usize, BTreeMap<String, Fill>>,
        currencies: &BTreeMap<u64, SettlementCurrency>,
        limits: simulation_runtime::checkpoint::Limits,
        maximum_total_bytes: usize,
    ) -> Result<Vec<simulation_runtime::checkpoint::Bundle>> {
        if maximum_total_bytes == 0
            || maximum_total_bytes > 64 * 1024 * 1024
            || last_fills
                .keys()
                .any(|index| *index >= self.controllers.len())
        {
            return Err(Error::Capacity(
                "multi-execution checkpoint budget or shard".into(),
            ));
        }
        let (selected, controller) = self
            .selected()?
            .ok_or_else(|| Error::Unready("multi-execution selected boundary absent".into()))?;
        let boundary = controller.decision_view()?.pending()?.ok_or_else(|| {
            Error::Unready("multi-execution selected market boundary absent".into())
        })?;
        if cut.boundary_hash != boundary.id
            || cut.at_ns != boundary.evaluated_at_ns
            || controller.status().acknowledged_boundaries.checked_add(1)
                != Some(cut.boundary_sequence)
        {
            return Err(Error::Conflict("multi-execution global cut differs".into()));
        }
        for lane in &self.controllers {
            lane.actions.require_complete()?;
        }
        self.require_complete_portfolio(portfolio, manifest, currencies)?;
        let empty = BTreeMap::new();
        let mut used = 0usize;
        let mut images = Vec::with_capacity(self.controllers.len());
        for (index, lane) in self.controllers.iter().enumerate() {
            let left = maximum_total_bytes
                .checked_sub(used)
                .filter(|left| *left > 0)
                .ok_or_else(|| Error::Capacity("multi-execution checkpoint total bytes".into()))?;
            let lane_limits = simulation_runtime::checkpoint::Limits {
                maximum_bytes: left.min(limits.maximum_bytes),
                ..limits
            };
            let fills = last_fills.get(&index).unwrap_or(&empty);
            let image = if index == selected {
                lane.execution
                    .checkpoint(manifest, cut, fills, lane_limits)?
            } else {
                lane.execution
                    .checkpoint_standby(manifest, cut, &lane.run, fills, lane_limits)?
            };
            used = image.objects.values().try_fold(
                used.checked_add(image.root.payload.len())
                    .ok_or_else(|| Error::Capacity("multi-execution size overflow".into()))?,
                |total, object| {
                    total
                        .checked_add(object.payload.len())
                        .ok_or_else(|| Error::Capacity("multi-execution size overflow".into()))
                },
            )?;
            if used > maximum_total_bytes {
                return Err(Error::Capacity(
                    "multi-execution checkpoint total bytes".into(),
                ));
            }
            images.push(image);
        }
        Ok(images)
    }

    /// Capture every Strategy 350 account owner at the selected global cut.
    /// Standby market heads may be preloaded, but their account decisions must
    /// remain at their own acknowledged frontiers.
    pub fn capture_strategy350_shards<S: Clone + serde::Serialize>(
        &self,
        owners: &BTreeMap<u64, super::strategy350_accounts::Accounts<S>>,
        cut: &arte_core::portfolio::checkpoint::Cut,
        maximum_total_bytes: usize,
    ) -> Result<BTreeMap<u64, super::strategy350_accounts::checkpoint::Bundle>> {
        if maximum_total_bytes == 0 || maximum_total_bytes > 64 * 1024 * 1024 {
            return Err(Error::Capacity(
                "multi-strategy checkpoint byte budget".into(),
            ));
        }
        let (selected, controller) = self
            .selected()?
            .ok_or_else(|| Error::Unready("multi-strategy selected boundary absent".into()))?;
        let boundary = controller.decision_view()?.pending()?.ok_or_else(|| {
            Error::Unready("multi-strategy selected market boundary absent".into())
        })?;
        if cut.boundary_hash != boundary.id
            || cut.at_ns != boundary.evaluated_at_ns
            || controller.status().acknowledged_boundaries.checked_add(1)
                != Some(cut.boundary_sequence)
            || owners.len() != self.controllers.len()
        {
            return Err(Error::Conflict(
                "multi-strategy global cut or owners differ".into(),
            ));
        }
        let mut images = BTreeMap::new();
        let mut used = 0usize;
        for (index, lane) in self.controllers.iter().enumerate() {
            lane.actions.require_complete()?;
            let instrument = lane.market_scope().instrument;
            let owner = owners
                .get(&instrument)
                .ok_or_else(|| Error::Conflict("multi-strategy ticker owner missing".into()))?;
            let left = maximum_total_bytes
                .checked_sub(used)
                .filter(|left| *left > 0)
                .ok_or_else(|| Error::Capacity("multi-strategy checkpoint byte budget".into()))?;
            let image = if index == selected {
                owner.checkpoint(lane, left)?
            } else {
                owner.checkpoint_standby(lane, cut, left)?
            };
            used = image.accounts.values().try_fold(
                used.checked_add(image.root.payload.len())
                    .ok_or_else(|| Error::Capacity("multi-strategy size overflow".into()))?,
                |total, object| {
                    total
                        .checked_add(object.payload.len())
                        .ok_or_else(|| Error::Capacity("multi-strategy size overflow".into()))
                },
            )?;
            if used > maximum_total_bytes || images.insert(instrument, image).is_some() {
                return Err(Error::Capacity("multi-strategy duplicate or budget".into()));
            }
        }
        Ok(images)
    }

    /// Capture every market/barrier shard without advancing any future head.
    /// A later publisher must bind these images to the strategy, execution and
    /// portfolio images before offering whole-run recovery.
    pub fn capture_market_shards(
        &self,
        cut: &arte_core::portfolio::checkpoint::Cut,
        maximum_total_bytes: usize,
    ) -> Result<
        BTreeMap<
            u64,
            arte_core::market_structure::scheduler::playback::accounts::checkpoint::Bundle,
        >,
    > {
        if maximum_total_bytes == 0 || maximum_total_bytes > 64 * 1024 * 1024 {
            return Err(Error::Capacity(
                "multi-market checkpoint byte budget".into(),
            ));
        }
        let (selected, controller) = self
            .selected()?
            .ok_or_else(|| Error::Unready("multi-market selected boundary absent".into()))?;
        let boundary = controller
            .decision_view()?
            .pending()?
            .ok_or_else(|| Error::Unready("multi-market selected head absent".into()))?;
        if cut.boundary_hash != boundary.id
            || cut.at_ns != boundary.evaluated_at_ns
            || controller.status().acknowledged_boundaries.checked_add(1)
                != Some(cut.boundary_sequence)
        {
            return Err(Error::Conflict("multi-market global cut differs".into()));
        }
        let mut images = BTreeMap::new();
        let mut used = 0usize;
        for (index, lane) in self.controllers.iter().enumerate() {
            lane.actions.require_complete()?;
            let scope = lane.market_scope();
            let head = lane
                .run
                .pending()?
                .ok_or_else(|| Error::Unready("multi-market shard head absent".into()))?;
            if index != selected
                && (head.evaluated_at_ns < cut.at_ns
                    || lane.status().acknowledged_boundaries.checked_add(1) != Some(head.sequence))
            {
                return Err(Error::Conflict("multi-market standby head differs".into()));
            }
            let context = content_hash(&(
                "arte.multi-market-shard-cut.v1",
                lane.manifest_hash(),
                cut,
                scope.provider,
                scope.instrument,
                scope.session,
                head.id,
                head.sequence,
            ))?;
            let left = maximum_total_bytes
                .checked_sub(used)
                .filter(|left| *left > 0)
                .ok_or_else(|| Error::Capacity("multi-market checkpoint byte budget".into()))?;
            let image = lane.run.checkpoint(&context, left)?;
            let scheduler = &image.playback.scheduler;
            used = [
                &image.root,
                &image.playback.root,
                &scheduler.root,
                &scheduler.market,
                &scheduler.trades,
                &scheduler.quotes,
                &scheduler.book,
            ]
            .into_iter()
            .chain(image.barrier.iter())
            .try_fold(used, |total, object| {
                total
                    .checked_add(object.payload.len())
                    .ok_or_else(|| Error::Capacity("multi-market size overflow".into()))
            })?;
            if used > maximum_total_bytes || images.insert(scope.instrument, image).is_some() {
                return Err(Error::Capacity("multi-market duplicate or budget".into()));
            }
        }
        Ok(images)
    }

    /// Test-only visibility for proving unselected shards cannot dispatch
    /// execution. Production callers may inspect only the selected controller.
    #[cfg(test)]
    pub(crate) fn controllers(&self) -> &[Runtime] {
        &self.controllers
    }

    pub fn resume_all(&mut self) -> Result<()> {
        if self.selected.is_some() {
            return Err(Error::Unready(
                "multi-controller boundary awaits journal".into(),
            ));
        }
        for controller in &mut self.controllers {
            if controller.status().mode == Mode::Paused {
                controller.resume()?;
            }
        }
        Ok(())
    }

    pub fn selected(&self) -> Result<Option<(usize, &Runtime)>> {
        let Some((index, id)) = &self.selected else {
            return Ok(None);
        };
        let controller = &self.controllers[*index];
        if controller
            .decision_view()?
            .pending()?
            .is_none_or(|boundary| boundary.id != id)
        {
            return Err(Error::Conflict(
                "multi-controller selected boundary changed".into(),
            ));
        }
        Ok(Some((*index, controller)))
    }

    pub async fn commit_fill(
        &mut self,
        shard: usize,
        publisher: &mut impl crate::fill_journal::Publisher,
    ) -> Result<bool> {
        if self.selected.as_ref().map(|(index, _)| *index) != Some(shard)
            || self.next_fill()?.is_none_or(|(index, _)| index != shard)
        {
            return Err(Error::Unready("multi-controller fill shard not due".into()));
        }
        self.controllers[shard].commit_fills(publisher).await
    }

    pub async fn service_selected_boundary<F, D, R>(
        &mut self,
        candidates: &mut Candidates,
        inputs: Inputs<'_>,
        journals: Journals<'_, F, D, R>,
    ) -> Result<Step>
    where
        F: crate::fill_journal::Publisher,
        D: crate::strategy_journal::Publisher,
        R: crate::rejection_journal::Publisher,
    {
        let (index, _) = self
            .selected()?
            .ok_or_else(|| Error::Unready("multi-controller selected boundary absent".into()))?;
        self.controllers[index]
            .service_boundary(candidates, inputs, journals)
            .await
    }

    pub async fn service_selected_strategy350_boundary<S, F, D, R>(
        &mut self,
        accounts: &mut super::strategy350_accounts::Accounts<S>,
        inputs: Inputs<'_>,
        journals: Journals<'_, F, D, R>,
    ) -> Result<Step>
    where
        S: Clone + serde::Serialize,
        F: crate::fill_journal::Publisher,
        D: crate::strategy_journal::Publisher,
        R: crate::rejection_journal::Publisher,
    {
        let (index, _) = self
            .selected()?
            .ok_or_else(|| Error::Unready("multi-controller selected boundary absent".into()))?;
        self.controllers[index]
            .service_strategy350_boundary(accounts, inputs, journals)
            .await
    }

    #[allow(clippy::too_many_arguments)]
    pub fn capture_selected(
        &mut self,
        candidates: &mut Candidates,
        portfolio: &mut Portfolio,
        manifest: &Pinned,
        cut: &arte_core::portfolio::checkpoint::Cut,
        last_fills: &BTreeMap<String, Fill>,
        currencies: &BTreeMap<u64, SettlementCurrency>,
        limits: &Limits,
    ) -> Result<Finalized> {
        let (index, _) = self
            .selected()?
            .ok_or_else(|| Error::Unready("multi-controller selected boundary absent".into()))?;
        Finalized::capture(
            Owners {
                controller: &mut self.controllers[index],
                candidates,
                portfolio,
                manifest,
                last_fills,
                currencies,
                limits,
            },
            cut,
        )
    }

    pub fn poll(&mut self) -> Result<MultiPoll> {
        if let Some((index, _)) = &self.selected {
            let controller = &self.controllers[*index];
            if controller.run.pending()?.is_none_or(|boundary| {
                self.selected
                    .as_ref()
                    .is_none_or(|(_, id)| boundary.id != id)
            }) {
                return Err(Error::Conflict(
                    "multi-controller selected boundary changed".into(),
                ));
            }
            if let Some((shard, scope_hash)) = self.next_fill()? {
                return Ok(MultiPoll::NeedsFills { shard, scope_hash });
            }
            return Ok(MultiPoll::Boundary { shard: *index });
        }
        for controller in &self.controllers {
            if controller.next_fill_scope_hash()?.is_some() {
                return Err(Error::Conflict(
                    "multi-controller unselected fill pending".into(),
                ));
            }
        }
        let mut paused = false;
        let mut yielding = false;
        let mut complete = 0usize;
        for controller in &mut self.controllers {
            // Select the global head before advancing any shard's execution
            // clock or producing quote fills. Market heads remain independent.
            match controller.run.poll()? {
                Poll::Paused => paused = true,
                Poll::Yield => yielding = true,
                Poll::Complete => complete += 1,
                Poll::Boundary => {}
            }
        }
        if paused {
            return Ok(MultiPoll::Paused);
        }
        if yielding {
            return Ok(MultiPoll::Yield);
        }
        if complete == self.controllers.len() {
            return Ok(MultiPoll::Complete);
        }
        let mut heads = Vec::new();
        for (index, controller) in self.controllers.iter().enumerate() {
            if controller.status().mode == Mode::Complete {
                continue;
            }
            let boundary = controller.run.pending()?.ok_or_else(|| {
                Error::Conflict("multi-controller pending boundary absent".into())
            })?;
            let scope = controller.market_scope();
            heads.push((
                (
                    boundary.evaluated_at_ns,
                    boundary.input(String::new()).event_time_ns,
                    scope.provider,
                    scope.instrument,
                    scope.session,
                    boundary.sequence,
                ),
                index,
                boundary.id,
            ));
        }
        let (index, id) = choose_head(heads)
            .ok_or_else(|| Error::Conflict("multi-controller has no pending boundary".into()))?;
        if self.controllers[index].poll()? != Poll::Boundary {
            return Err(Error::Conflict(
                "multi-controller selected head changed".into(),
            ));
        }
        self.selected = Some((index, id));
        if let Some((shard, scope_hash)) = self.next_fill()? {
            return Ok(MultiPoll::NeedsFills { shard, scope_hash });
        }
        Ok(MultiPoll::Boundary { shard: index })
    }

    fn next_fill(&self) -> Result<Option<(usize, String)>> {
        self.selected
            .as_ref()
            .map(|(index, _)| {
                self.controllers[*index]
                    .next_fill_scope_hash()
                    .map(|scope| scope.map(|scope_hash| (*index, scope_hash)))
            })
            .transpose()
            .map(Option::flatten)
    }

    /// Generic-candidate release requires the existing verified published
    /// common-cut receipt. Strategy 350 needs its own equivalent publication
    /// graph before this coordinator may release its exposure decisions.
    #[allow(clippy::too_many_arguments)]
    pub fn acknowledge_selected_published(
        &mut self,
        published: &Published,
        candidates: &mut Candidates,
        portfolio: &mut Portfolio,
        manifest: &Pinned,
        last_fills: &BTreeMap<String, Fill>,
        currencies: &BTreeMap<u64, SettlementCurrency>,
        limits: &Limits,
    ) -> Result<()> {
        let (index, id) = self
            .selected
            .as_ref()
            .ok_or_else(|| Error::Unready("multi-controller boundary absent".into()))?;
        let controller = &mut self.controllers[*index];
        let boundary = controller
            .decision_view()?
            .pending()?
            .ok_or_else(|| Error::Unready("multi-controller selected boundary absent".into()))?;
        if boundary.id != id
            || published.cut().boundary_hash != *id
            || published.cut().at_ns != boundary.evaluated_at_ns
            || controller.status().acknowledged_boundaries.checked_add(1)
                != Some(published.cut().boundary_sequence)
        {
            return Err(Error::Conflict(
                "multi-controller published cut differs".into(),
            ));
        }
        published.acknowledge(Owners {
            controller,
            candidates,
            portfolio,
            manifest,
            last_fills,
            currencies,
            limits,
        })?;
        self.selected = None;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn head_choice_is_clock_then_source_then_scope_then_sequence() {
        let heads = [
            ((20, 10, 1, 2, 20260923, 1), 2, "later"),
            ((10, 9, 1, 2, 20260923, 1), 1, "second"),
            ((10, 9, 1, 1, 20260923, 2), 0, "first"),
        ];
        assert_eq!(choose_head(heads), Some((0, "first".into())));
    }
}
