//! Account-owned Strategy 350 state for historical playback. Market state and
//! selected event proofs remain ticker-shared. No broker capability exists here.
use super::Runtime as Playback;
use crate::strategy_journal::{self, accounts::Boundary, Publisher};
use arte_core::{
    content_hash,
    execution_interval::{ExecutableKind, ExecutionContract, Route},
    run_manifest::Pinned,
    strategy350_effective::Config,
    strategy350_transaction::{self, HistoricalMarketDecisionInput},
    strategy_dispatch::{Action, Decision, Mode, StrategyKind},
    strategy_transaction::{Committed, Runtime},
    Error, Result,
};
use futures_util::{stream, StreamExt};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

struct Slot<S> {
    runtime: Runtime<S>,
    effective: Config,
    route: Route,
    receipt: Option<Committed>,
}

pub struct Accounts<S> {
    manifest_hash: String,
    slots: BTreeMap<String, Slot<S>>,
}

pub struct Outcome {
    pub scope_hash: String,
    pub result: Result<()>,
}

fn restrict_actions(actions: Vec<Action>) -> Result<Vec<Action>> {
    if actions.iter().any(|action| {
        !matches!(
            action,
            Action::Wait { .. }
                | Action::Hold { .. }
                | Action::CancelEntry { .. }
                | Action::Exit {
                    quantity: 1..,
                    reduce_only: true,
                    ..
                }
        )
    }) {
        return Err(Error::Unready("Strategy 350 evaluator incomplete".into()));
    }
    Ok(actions)
}

impl<S: Clone + Serialize> Accounts<S> {
    /// The maps are keyed by the exact manifest-derived strategy scope hash.
    /// This constructor never discovers a configuration or shares account state.
    pub fn new(
        manifest: &Pinned,
        instrument: u64,
        mut configurations: BTreeMap<String, Config>,
        mut initial_states: BTreeMap<String, S>,
        maximum_state_bytes: usize,
    ) -> Result<Self> {
        if manifest.manifest().mode != Mode::Backtest
            || manifest.manifest().clock != arte_core::run_manifest::Clock::Historical
            || instrument == 0
            || manifest.manifest().consumers.is_empty()
            || maximum_state_bytes == 0
            || maximum_state_bytes > 64 * 1024 * 1024
        {
            return Err(Error::Invalid("Strategy 350 account owner bounds".into()));
        }
        let mut slots = BTreeMap::new();
        for consumer in manifest
            .manifest()
            .consumers
            .iter()
            .filter(|consumer| consumer.instrument == instrument)
        {
            if consumer.strategy_kind != StrategyKind::Strategy350 {
                return Err(Error::Conflict(
                    "Strategy 350 account consumer differs".into(),
                ));
            }
            let scope = manifest.scope(
                &consumer.account,
                consumer.instrument,
                &consumer.strategy_instance,
            )?;
            let key = content_hash(&scope)?;
            let effective = configurations.remove(&key).ok_or_else(|| {
                Error::Unready("Strategy 350 effective configuration missing".into())
            })?;
            effective.require_scope(&scope)?;
            let route = Route::new(&ExecutionContract {
                kind: ExecutableKind::Strategy,
                id: scope.strategy_instance.clone(),
                implementation_hash: scope.config_hash.clone(),
                interval: effective.execution_interval,
            })?;
            let state = initial_states.remove(&key).ok_or_else(|| {
                Error::Unready("Strategy 350 initial account state missing".into())
            })?;
            if slots
                .insert(
                    key,
                    Slot {
                        runtime: Runtime::new(scope, state, maximum_state_bytes)?,
                        effective,
                        route,
                        receipt: None,
                    },
                )
                .is_some()
            {
                return Err(Error::Conflict(
                    "duplicate Strategy 350 account scope".into(),
                ));
            }
        }
        if slots.is_empty() || !configurations.is_empty() || !initial_states.is_empty() {
            return Err(Error::Conflict(
                "Strategy 350 local account scope set differs".into(),
            ));
        }
        Ok(Self {
            manifest_hash: manifest.hash().into(),
            slots,
        })
    }

    pub fn scope_hashes(&self) -> impl Iterator<Item = &str> {
        self.slots.keys().map(String::as_str)
    }

    pub fn state(&self, scope_hash: &str) -> Result<&S> {
        self.slots
            .get(scope_hash)
            .map(|slot| slot.runtime.committed_state())
            .ok_or_else(|| Error::Invalid("Strategy 350 account scope missing".into()))
    }

    /// A common-cut publisher must see every current account receipt. An old
    /// boundary's retained receipt cannot satisfy a new market cut.
    pub fn registered_receipts(&self, controller: &Playback) -> Result<Vec<&Committed>> {
        self.require_controller(controller)?;
        let view = controller.decision_view()?;
        let boundary = view
            .pending()?
            .ok_or_else(|| Error::Unready("Strategy 350 playback boundary missing".into()))?;
        let mut receipts = Vec::with_capacity(self.slots.len());
        for slot in self.slots.values() {
            if !view.is_due(slot.runtime.scope())? {
                continue;
            }
            if view.needs_decision(slot.runtime.scope())? {
                return Err(Error::Unready(
                    "Strategy 350 account receipt pending".into(),
                ));
            }
            let receipt = slot
                .receipt
                .as_ref()
                .filter(|receipt| receipt.decision().input.event_id == boundary.id)
                .ok_or_else(|| Error::Unready("Strategy 350 current receipt missing".into()))?;
            Boundary::validate_decision(controller, receipt.decision())?;
            receipts.push(receipt);
        }
        Ok(receipts)
    }

    pub fn needed_evaluations(&self, controller: &Playback) -> Result<Vec<String>> {
        self.require_controller(controller)?;
        let view = controller.decision_view()?;
        let boundary = view
            .pending()?
            .ok_or_else(|| Error::Unready("Strategy 350 playback boundary missing".into()))?;
        let mut needed = Vec::new();
        for (key, slot) in &self.slots {
            if !view.needs_decision(slot.runtime.scope())? {
                continue;
            }
            if let Some(receipt) = slot
                .receipt
                .as_ref()
                .filter(|receipt| receipt.decision().input.event_id == boundary.id)
            {
                Boundary::validate_decision(controller, receipt.decision())?;
            } else if let Some(batch) = slot.runtime.pending_batch() {
                if batch.records().len() != 1 {
                    return Err(Error::Invalid("Strategy 350 pending batch count".into()));
                }
                Boundary::validate_decision(controller, &batch.records()[0].decode()?)?;
            } else {
                needed.push(key.clone());
            }
        }
        Ok(needed)
    }

    pub(super) fn require_controller(&self, controller: &Playback) -> Result<()> {
        if controller.run.manifest_hash() != self.manifest_hash
            || controller.run.scopes().len() != self.slots.len()
        {
            return Err(Error::Conflict(
                "Strategy 350 playback owner differs".into(),
            ));
        }
        for scope in controller.run.scopes() {
            if self
                .slots
                .get(&content_hash(scope)?)
                .is_none_or(|slot| slot.runtime.scope() != scope)
            {
                return Err(Error::Conflict(
                    "Strategy 350 playback scope differs".into(),
                ));
            }
        }
        Ok(())
    }

    /// The complete Strategy 350 evaluator is not yet connected. Until it is,
    /// this owner can journal only waits and exposure-reducing exits.
    pub fn prepare_historical(
        &mut self,
        controller: &Playback,
        scope_hash: &str,
        request: HistoricalMarketDecisionInput<'_>,
        observe: impl FnOnce(&mut S) -> Result<()>,
        calculate: impl FnOnce(&mut S) -> Result<Vec<Action>>,
    ) -> Result<Decision> {
        self.require_controller(controller)?;
        let view = controller.decision_view()?;
        let boundary = view
            .pending()?
            .ok_or_else(|| Error::Unready("Strategy 350 playback boundary missing".into()))?;
        let expected = boundary.input(request.input.feature_hash.clone());
        if request.input.event_id != expected.event_id
            || request.input.event_time_ns != expected.event_time_ns
            || request.input.available_at_ns != expected.available_at_ns
            || request.input.evaluated_at_ns != expected.evaluated_at_ns
            || request.input.source_sequence != expected.source_sequence
        {
            return Err(Error::Conflict(
                "Strategy 350 playback input differs".into(),
            ));
        }
        let slot = self
            .slots
            .get_mut(scope_hash)
            .ok_or_else(|| Error::Invalid("Strategy 350 account scope missing".into()))?;
        if !view.needs_decision(slot.runtime.scope())?
            || request.effective.hash()? != slot.effective.hash()?
        {
            return Err(Error::Conflict(
                "Strategy 350 account decision differs".into(),
            ));
        }
        if !boundary.due_for(slot.route) {
            return Err(Error::Unready(
                "Strategy 350 decision boundary is outside declared interval".into(),
            ));
        }
        let decision = strategy350_transaction::prepare_historical_market_decision(
            &mut slot.runtime,
            request,
            observe,
            |state| restrict_actions(calculate(state)?),
        )?;
        Boundary::validate_decision(controller, &decision)?;
        Ok(decision)
    }

    /// Every requested account is preflighted before any journal I/O. Independent
    /// publishers run concurrently, then receipts enter the shared barrier.
    pub async fn commit_accounts<P: Publisher>(
        &mut self,
        controller: &mut Playback,
        publishers: &mut BTreeMap<String, P>,
        concurrency: usize,
    ) -> Result<Vec<Outcome>> {
        self.require_controller(controller)?;
        if concurrency == 0 || concurrency > 64 || !publishers.keys().eq(self.slots.keys()) {
            return Err(Error::Invalid("Strategy 350 journal publisher set".into()));
        }
        let view = controller.decision_view()?;
        let id = view
            .pending()?
            .ok_or_else(|| Error::Unready("Strategy 350 playback boundary missing".into()))?
            .id
            .to_owned();
        let mut selected = BTreeSet::new();
        for (key, slot) in &self.slots {
            if !view.needs_decision(slot.runtime.scope())? {
                continue;
            }
            let decision = if let Some(receipt) = slot
                .receipt
                .as_ref()
                .filter(|receipt| receipt.decision().input.event_id == id)
            {
                receipt.decision().clone()
            } else {
                let batch = slot
                    .runtime
                    .pending_batch()
                    .ok_or_else(|| Error::Unready("Strategy 350 decision not prepared".into()))?;
                if batch.records().len() != 1 {
                    return Err(Error::Invalid("Strategy 350 decision batch count".into()));
                }
                batch.records()[0].decode()?
            };
            Boundary::validate_decision(controller, &decision)?;
            selected.insert(key.clone());
        }
        let mut work = stream::iter(self.slots.iter_mut().zip(publishers.iter_mut()))
            .filter(|((key, _), _)| futures_util::future::ready(selected.contains(*key)))
            .map(|((key, slot), (_, publisher))| {
                let id = &id;
                async move {
                    let result = if slot
                        .receipt
                        .as_ref()
                        .is_some_and(|receipt| receipt.decision().input.event_id == *id)
                    {
                        Ok(())
                    } else {
                        match strategy_journal::commit(&mut slot.runtime, publisher).await {
                            Ok(receipt) => {
                                slot.receipt = Some(receipt);
                                Ok(())
                            }
                            Err(error) => Err(error),
                        }
                    };
                    (key.clone(), slot, result)
                }
            })
            .buffer_unordered(concurrency);
        let mut outcomes = Vec::new();
        while let Some((scope_hash, slot, result)) = work.next().await {
            let result = result.and_then(|()| {
                Boundary::record(controller, slot.receipt.as_ref().unwrap()).map(|_| ())
            });
            outcomes.push(Outcome { scope_hash, result });
        }
        outcomes.sort_by(|a, b| a.scope_hash.cmp(&b.scope_hash));
        Ok(outcomes)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::{
        execution_interval::ExecutionInterval,
        run_manifest::{Clock, Consumer, Execution, Manifest},
        strategy350_gap,
    };

    fn effective() -> Config {
        Config {
            execution_interval: ExecutionInterval::Events,
            gap: strategy350_gap::Config {
                execution_interval: ExecutionInterval::Fixed(100_000_000),
                maximum_levels: 1_000,
            },
            signal_config_hash: "a".repeat(64),
            screen_config_hash: "b".repeat(64),
            price_gate_config_hash: "c".repeat(64),
            macd_config_hash: "d".repeat(64),
            noise_config_hash: "e".repeat(64),
            bos_config_hash: "f".repeat(64),
            level_book_config_hash: "1".repeat(64),
            rule_set_hash: "2".repeat(64),
            account_risk_hash: "3".repeat(64),
            watchlist_config_hash: None,
        }
    }

    fn manifest() -> Pinned {
        let config_hash = effective().hash().unwrap();
        let manifest = Manifest {
            schema_version: 3,
            run_id: "strategy-350-offline".into(),
            mode: Mode::Backtest,
            code_release_hash: "a".repeat(64),
            source_manifest_hash: "b".repeat(64),
            reference_manifest_hash: "c".repeat(64),
            seed_manifest_hash: "d".repeat(64),
            algorithm_manifest_hash: "e".repeat(64),
            dependency_plan_hash: "f".repeat(64),
            hardware_profile_hash: "1".repeat(64),
            clock: Clock::Historical,
            execution: Execution::Simulated {
                fill_model_hash: "2".repeat(64),
                cost_model_hash: "3".repeat(64),
            },
            consumers: ["first", "second"]
                .into_iter()
                .map(|account| Consumer {
                    account: account.into(),
                    instrument: 10,
                    strategy_instance: "strategy-350".into(),
                    strategy_kind: StrategyKind::Strategy350,
                    execution_interval: ExecutionInterval::Events,
                    effective_config_hash: config_hash.clone(),
                })
                .collect(),
        };
        let hash = manifest.hash().unwrap();
        Pinned::new(manifest, &hash).unwrap()
    }

    fn inputs(manifest: &Pinned) -> (BTreeMap<String, Config>, BTreeMap<String, u64>) {
        let mut configs = BTreeMap::new();
        let mut states = BTreeMap::new();
        for account in ["first", "second"] {
            let scope = manifest.scope(account, 10, "strategy-350").unwrap();
            let key = content_hash(&scope).unwrap();
            configs.insert(key.clone(), effective());
            states.insert(key, if account == "first" { 1 } else { 2 });
        }
        (configs, states)
    }

    #[test]
    fn separate_account_state_and_exact_effective_config_are_required() {
        let manifest = manifest();
        let (configs, states) = inputs(&manifest);
        let accounts = Accounts::new(&manifest, 10, configs.clone(), states.clone(), 1024).unwrap();
        assert_eq!(accounts.scope_hashes().count(), 2);
        assert!(accounts
            .slots
            .values()
            .all(|slot| slot.route.interval() == ExecutionInterval::Events));
        for account in ["first", "second"] {
            let scope = manifest.scope(account, 10, "strategy-350").unwrap();
            assert_eq!(
                *accounts.state(&content_hash(&scope).unwrap()).unwrap(),
                if account == "first" { 1 } else { 2 }
            );
        }
        let mut missing = configs.clone();
        missing.pop_first();
        assert!(Accounts::new(&manifest, 10, missing, states.clone(), 1024).is_err());
        let mut changed = configs.clone();
        changed.values_mut().next().unwrap().gap.maximum_levels += 1;
        assert!(Accounts::new(&manifest, 10, changed, states.clone(), 1024).is_err());
        let mut surplus = states.clone();
        surplus.insert("0".repeat(64), 3);
        assert!(Accounts::new(&manifest, 10, configs.clone(), surplus, 1024).is_err());
        assert!(Accounts::new(&manifest, 11, configs, states, 1024).is_err());
        let mut recorded = manifest.manifest().clone();
        recorded.clock = Clock::RecordedLive;
        let hash = recorded.hash().unwrap();
        let recorded = Pinned::new(recorded, &hash).unwrap();
        let (configs, states) = inputs(&recorded);
        assert!(Accounts::new(&recorded, 10, configs, states, 1024).is_err());
    }

    #[test]
    fn account_owner_selects_only_its_ticker_from_a_shared_run_manifest() {
        let base = manifest();
        let mut combined = base.manifest().clone();
        let mut other = combined.consumers[0].clone();
        other.instrument = 20;
        other.account = "third".into();
        combined.consumers.push(other);
        let hash = combined.hash().unwrap();
        let combined = Pinned::new(combined, &hash).unwrap();
        let (configs, states) = inputs(&combined);
        let local = Accounts::new(&combined, 10, configs.clone(), states.clone(), 1024).unwrap();
        assert_eq!(local.scope_hashes().count(), 2);
        assert!(Accounts::new(&combined, 20, configs.clone(), states.clone(), 1024).is_err());
        let mut surplus = configs.clone();
        let other_scope = combined.scope("third", 20, "strategy-350").unwrap();
        surplus.insert(content_hash(&other_scope).unwrap(), effective());
        assert!(Accounts::new(&combined, 10, surplus, states.clone(), 1024).is_err());
        let mut mixed = combined.manifest().clone();
        mixed.consumers[0].strategy_kind = StrategyKind::GenericCandidate;
        let hash = mixed.hash().unwrap();
        let mixed = Pinned::new(mixed, &hash).unwrap();
        assert!(Accounts::new(&mixed, 10, configs, states, 1024).is_err());
    }

    #[test]
    fn incomplete_evaluator_only_allows_waits_and_reduce_only_exits() {
        let wait = Action::Wait {
            reason: "pending".into(),
        };
        assert_eq!(restrict_actions(vec![wait]).unwrap().len(), 1);
        let exit = Action::Exit {
            reason: arte_core::strategy_dispatch::ExitReason::ManualExit,
            quantity: 1,
            reduce_only: true,
        };
        assert_eq!(restrict_actions(vec![exit]).unwrap().len(), 1);
        let unsafe_exit = Action::Exit {
            reason: arte_core::strategy_dispatch::ExitReason::ManualExit,
            quantity: 1,
            reduce_only: false,
        };
        assert!(restrict_actions(vec![unsafe_exit]).is_err());
        assert!(restrict_actions(vec![Action::Exit {
            reason: arte_core::strategy_dispatch::ExitReason::ManualExit,
            quantity: 0,
            reduce_only: true,
        }])
        .is_err());
    }
}
