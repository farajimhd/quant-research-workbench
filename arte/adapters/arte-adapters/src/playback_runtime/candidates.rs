//! Owned candidate and feature state for one instrument, shared across accounts.
//! Market progression and execution authority remain in the playback controller.
use super::*;
use arte_core::{
    candidate_features as features, candidate_runtime::Runtime as Candidate, content_hash,
    run_manifest::Pinned, strategy_candidate as candidate, strategy_dispatch::Safety,
    strategy_lifecycle::RecoveryPolicy,
};
use futures_util::{stream, StreamExt};
use std::collections::{BTreeMap, BTreeSet};
pub mod checkpoint;

struct Slot {
    runtime: Candidate,
    receipt: Option<Committed>,
}
pub struct Candidates {
    manifest_hash: String,
    features: features::State,
    slots: BTreeMap<String, Slot>,
    configurations: BTreeMap<String, arte_core::candidate_config::Config>,
}
pub struct Outcome {
    pub scope_hash: String,
    pub result: Result<()>,
}
pub struct EntryEvidence<'a> {
    pub admission: &'a arte_core::strategy_entry::Admission,
    pub swings: &'a [arte_core::strategy_targets::Swing],
    pub regular: bool,
    pub regular_target: Option<f64>,
}
/// Explicit external evidence for either price evaluator. The pending market
/// boundary, not the caller, chooses which evaluator may consume it.
pub struct Evidence<'a> {
    pub entry: EntryEvidence<'a>,
    pub acquisition: features::AcquisitionContext,
    pub adds: &'a arte_core::strategy_adds::Gates,
}
impl Candidates {
    pub fn prepare_owned(
        &mut self,
        controller: &Runtime,
        scope: &str,
        evidence: Evidence<'_>,
        external_safety: &Safety,
    ) -> Result<Decision> {
        self.require(controller)?;
        let slot = self
            .slots
            .get(scope)
            .ok_or_else(|| Error::Invalid("candidate consumer missing".into()))?;
        let reconciled = controller.owned_candidate_position(slot.runtime.scope())?;
        self.prepare_configured(
            controller,
            scope,
            evidence,
            &reconciled.safety(external_safety),
            &reconciled.position,
        )
    }
    pub fn prepare_reconciled(
        &mut self,
        controller: &Runtime,
        scope: &str,
        evidence: Evidence<'_>,
        external_safety: &Safety,
        target: Option<&arte_core::strategy_protection::ActiveTarget>,
    ) -> Result<Decision> {
        self.require(controller)?;
        let slot = self
            .slots
            .get(scope)
            .ok_or_else(|| Error::Invalid("candidate consumer missing".into()))?;
        let reconciled = controller.candidate_position(slot.runtime.scope(), target)?;
        self.prepare_configured(
            controller,
            scope,
            evidence,
            &reconciled.safety(external_safety),
            &reconciled.position,
        )
    }
    /// Read only the supplied configuration stream. No filesystem discovery,
    /// environment fallback, network access or service startup occurs here.
    pub fn from_reader(
        controller: &Runtime,
        manifest: &Pinned,
        reader: impl std::io::Read,
        maximum_state_bytes: usize,
    ) -> Result<Self> {
        use arte_core::candidate_config::document::{Document, MAXIMUM_BYTES};
        use std::io::Read;
        let mut bytes = Vec::new();
        reader
            .take(MAXIMUM_BYTES as u64 + 1)
            .read_to_end(&mut bytes)
            .map_err(|error| Error::Unready(format!("candidate policy read: {error}")))?;
        Self::configured(
            controller,
            manifest,
            Document::decode(&bytes)?.bind(manifest)?,
            maximum_state_bytes,
        )
    }
    pub fn prepare_configured(
        &mut self,
        controller: &Runtime,
        scope: &str,
        evidence: Evidence<'_>,
        safety: &Safety,
        broker: &candidate::PositionObservation,
    ) -> Result<Decision> {
        self.require(controller)?;
        if !self.configurations.contains_key(scope) {
            return Err(Error::Unready("candidate policy not bound".into()));
        }
        use arte_core::market_structure::scheduler::Kind;
        let view = controller.decision_view()?;
        let boundary = view
            .pending()?
            .ok_or_else(|| Error::Unready("no playback boundary".into()))?;
        match boundary.kind {
            Kind::Completed {
                interval_ns: 1_000_000_000,
                ..
            } => self.prepare_configured_completed(
                controller,
                scope,
                evidence.entry,
                safety,
                broker,
                evidence.adds,
            ),
            Kind::Trade { eligible: true, .. } => self.prepare_configured_intrabar(
                controller,
                scope,
                evidence.acquisition,
                safety,
                broker,
            ),
            _ => self.prepare_observation(controller, scope, safety, broker),
        }
    }
    pub fn configured(
        controller: &Runtime,
        manifest: &Pinned,
        configurations: BTreeMap<String, arte_core::candidate_config::Config>,
        maximum_state_bytes: usize,
    ) -> Result<Self> {
        let first = configurations
            .values()
            .next()
            .ok_or_else(|| Error::Invalid("candidate configurations missing".into()))?;
        let mut owner = Self::new(
            controller,
            manifest,
            first.features.clone(),
            maximum_state_bytes,
        )?;
        if !configurations.keys().eq(owner.slots.keys()) {
            return Err(Error::Conflict(
                "candidate configuration scope set differs".into(),
            ));
        }
        let quote_policy = controller.run.quotes()?.policy_hash()?;
        for (scope, config) in &configurations {
            if config.effective_hash(&owner.features, quote_policy)?
                != owner.slots[scope].runtime.scope().config_hash
            {
                return Err(Error::Conflict(
                    "candidate effective policy differs from manifest".into(),
                ));
            }
        }
        owner.configurations = configurations;
        Ok(owner)
    }
    pub fn new(
        controller: &Runtime,
        manifest: &Pinned,
        config: features::Config,
        maximum_state_bytes: usize,
    ) -> Result<Self> {
        let status = controller.status();
        if controller.run.manifest_hash() != manifest.hash()
            || status.admitted_events != 0
            || status.acknowledged_boundaries != 0
            || status.pending_boundary
        {
            return Err(Error::Conflict(
                "candidate owner requires matching unused controller".into(),
            ));
        }
        let features = features::State::new(controller.run.market()?, config)?;
        let mut slots = BTreeMap::new();
        for scope in controller.run.scopes() {
            let runtime = Candidate::from_manifest(
                manifest,
                &scope.account,
                scope.instrument,
                &scope.strategy_instance,
                candidate::State::default(),
                maximum_state_bytes,
            )?;
            slots.insert(
                content_hash(scope)?,
                Slot {
                    runtime,
                    receipt: None,
                },
            );
        }
        Ok(Self {
            manifest_hash: manifest.hash().into(),
            features,
            slots,
            configurations: BTreeMap::new(),
        })
    }
    fn require(&self, controller: &Runtime) -> Result<()> {
        if controller.run.manifest_hash() != self.manifest_hash
            || controller.run.market()?.source_scope() != self.features.source_scope()
            || controller.run.scopes().len() != self.slots.len()
        {
            return Err(Error::Conflict(
                "candidate controller identity differs".into(),
            ));
        }
        for scope in controller.run.scopes() {
            if self
                .slots
                .get(&content_hash(scope)?)
                .is_none_or(|slot| slot.runtime.scope() != scope)
            {
                return Err(Error::Conflict("candidate consumer differs".into()));
            }
        }
        Ok(())
    }
    pub fn scope_hashes(&self) -> impl Iterator<Item = &str> {
        self.slots.keys().map(String::as_str)
    }
    pub fn features(&self) -> &features::State {
        &self.features
    }
    pub fn state(&self, scope: &str) -> Result<&candidate::State> {
        self.slots
            .get(scope)
            .map(|s| s.runtime.state())
            .ok_or_else(|| Error::Invalid("candidate consumer missing".into()))
    }
    pub fn observe(&mut self, controller: &Runtime) -> Result<bool> {
        self.require(controller)?;
        controller
            .decision_view()?
            .observe_features(&mut self.features)
    }
    pub fn prepare_observation(
        &mut self,
        controller: &Runtime,
        scope: &str,
        safety: &Safety,
        broker: &candidate::PositionObservation,
    ) -> Result<Decision> {
        self.require(controller)?;
        let slot = self
            .slots
            .get_mut(scope)
            .ok_or_else(|| Error::Invalid("candidate consumer missing".into()))?;
        controller.decision_view()?.prepare_observation(
            &mut slot.runtime,
            &self.features,
            safety,
            broker,
        )
    }
    #[allow(clippy::too_many_arguments)]
    pub fn prepare_configured_completed(
        &mut self,
        controller: &Runtime,
        scope: &str,
        context: EntryEvidence<'_>,
        safety: &Safety,
        broker: &candidate::PositionObservation,
        gates: &arte_core::strategy_adds::Gates,
    ) -> Result<Decision> {
        self.require(controller)?;
        let config = self
            .configurations
            .get(scope)
            .ok_or_else(|| Error::Unready("candidate policy not bound".into()))?;
        let slot = self
            .slots
            .get_mut(scope)
            .ok_or_else(|| Error::Invalid("candidate consumer missing".into()))?;
        let recovery = slot.runtime.state().recovery.clone();
        let context = features::EntryContext {
            admission: context.admission,
            swings: context.swings,
            regular: context.regular,
            regular_target: context.regular_target,
            recovery: &recovery,
            recovery_policy: &config.recovery,
        };
        controller.decision_view()?.prepare_completed(
            &mut slot.runtime,
            &self.features,
            context,
            safety,
            broker,
            gates,
            &config.policy()?,
            &config.acquisition,
        )
    }
    pub fn prepare_configured_intrabar(
        &mut self,
        controller: &Runtime,
        scope: &str,
        context: features::AcquisitionContext,
        safety: &Safety,
        broker: &candidate::PositionObservation,
    ) -> Result<Decision> {
        self.require(controller)?;
        let config = self
            .configurations
            .get(scope)
            .ok_or_else(|| Error::Unready("candidate policy not bound".into()))?;
        let slot = self
            .slots
            .get_mut(scope)
            .ok_or_else(|| Error::Invalid("candidate consumer missing".into()))?;
        controller.decision_view()?.prepare_intrabar(
            &mut slot.runtime,
            &self.features,
            context,
            safety,
            broker,
            &config.policy()?,
            &config.acquisition,
            &config.recovery,
        )
    }
    #[allow(clippy::too_many_arguments)]
    pub fn prepare_completed(
        &mut self,
        controller: &Runtime,
        scope: &str,
        context: features::EntryContext<'_>,
        safety: &Safety,
        broker: &candidate::PositionObservation,
        gates: &arte_core::strategy_adds::Gates,
        policy: &candidate::Policy<'_>,
        intrabar: &candidate::AcquisitionPolicy,
    ) -> Result<Decision> {
        self.require(controller)?;
        let slot = self
            .slots
            .get_mut(scope)
            .ok_or_else(|| Error::Invalid("candidate consumer missing".into()))?;
        controller.decision_view()?.prepare_completed(
            &mut slot.runtime,
            &self.features,
            context,
            safety,
            broker,
            gates,
            policy,
            intrabar,
        )
    }
    #[allow(clippy::too_many_arguments)]
    pub fn prepare_intrabar(
        &mut self,
        controller: &Runtime,
        scope: &str,
        context: features::AcquisitionContext,
        safety: &Safety,
        broker: &candidate::PositionObservation,
        policy: &candidate::Policy<'_>,
        intrabar: &candidate::AcquisitionPolicy,
        recovery: &RecoveryPolicy,
    ) -> Result<Decision> {
        self.require(controller)?;
        let slot = self
            .slots
            .get_mut(scope)
            .ok_or_else(|| Error::Invalid("candidate consumer missing".into()))?;
        controller.decision_view()?.prepare_intrabar(
            &mut slot.runtime,
            &self.features,
            context,
            safety,
            broker,
            policy,
            intrabar,
            recovery,
        )
    }
    /// Publishers are keyed by exact scope hash. Receipts remain in owned slots
    /// across cancellation, including cancellation before barrier registration.
    pub async fn commit_accounts<P: crate::strategy_journal::Publisher>(
        &mut self,
        controller: &mut Runtime,
        publishers: &mut BTreeMap<String, P>,
        concurrency: usize,
    ) -> Result<Vec<Outcome>> {
        self.require(controller)?;
        if concurrency == 0 || concurrency > 64 || !publishers.keys().eq(self.slots.keys()) {
            return Err(Error::Invalid(
                "candidate publisher scopes or concurrency".into(),
            ));
        }
        let run = controller.decision_view()?;
        let id = run
            .pending()?
            .ok_or_else(|| Error::Unready("candidate boundary missing".into()))?
            .id
            .to_owned();
        let mut selected = BTreeSet::new();
        for (key, slot) in &self.slots {
            if !run.needs_decision(slot.runtime.scope())? {
                continue;
            }
            let decision = if let Some(receipt) = slot
                .receipt
                .as_ref()
                .filter(|r| r.decision().input.event_id == id)
            {
                receipt.decision().clone()
            } else {
                let batch = slot
                    .runtime
                    .pending_batch()
                    .ok_or_else(|| Error::Unready("candidate decision not prepared".into()))?;
                if batch.records().len() != 1 {
                    return Err(Error::Invalid("candidate batch count".into()));
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
                        .is_some_and(|r| r.decision().input.event_id == *id)
                    {
                        Ok(())
                    } else {
                        match crate::strategy_journal::commit(&mut slot.runtime, publisher).await {
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
