//! Manifest-bound account fan-out. No mutable playback escape hatch is exposed.
//! Journal receipts gate the market cursor; broker/fill completion is separate.
use super::{Boundary, Playback, Poll, Prepared, Runtime, Scheduler, Status};
use crate::{
    account_boundary::Barrier,
    run_manifest::Pinned,
    strategy_dispatch::{Mode, Scope},
    strategy_transaction::Committed,
    Error, Result,
};
pub mod checkpoint;

pub struct Run {
    playback: Playback,
    manifest_hash: String,
    scopes: Vec<Scope>,
    barrier: Option<Barrier>,
    maximum_consumers: usize,
}
impl Run {
    pub fn new(
        manifest: &Pinned,
        sources: &super::sources::Catalog,
        scheduler: Scheduler,
        prepared: Prepared,
        frames_per_poll: usize,
        maximum_consumers: usize,
    ) -> Result<Self> {
        sources.require(manifest, &prepared)?;
        if manifest.manifest().mode != Mode::Backtest
            || scheduler.run_id != manifest.manifest().run_id
        {
            return Err(Error::Conflict(
                "backtest playback run identity or mode".into(),
            ));
        }
        let scopes =
            Self::consumer_scopes(manifest, scheduler.scope().instrument, maximum_consumers)?;
        Ok(Self {
            playback: Playback::new(scheduler, prepared, frames_per_poll)?,
            manifest_hash: manifest.hash().into(),
            scopes,
            barrier: None,
            maximum_consumers,
        })
    }
    fn consumer_scopes(
        manifest: &Pinned,
        instrument: u64,
        maximum_consumers: usize,
    ) -> Result<Vec<Scope>> {
        if maximum_consumers == 0 || maximum_consumers > 4096 {
            return Err(Error::Capacity("backtest consumer budget".into()));
        }
        let mut scopes = Vec::new();
        for consumer in &manifest.manifest().consumers {
            if consumer.instrument == instrument {
                if scopes.len() == maximum_consumers {
                    return Err(Error::Capacity(
                        "declared consumers exceed playback budget".into(),
                    ));
                }
                scopes.push(manifest.scope(
                    &consumer.account,
                    instrument,
                    &consumer.strategy_instance,
                )?);
            }
        }
        if scopes.is_empty() {
            return Err(Error::Unready(
                "instrument has no declared strategy consumers".into(),
            ));
        }
        Ok(scopes)
    }
    pub fn manifest_hash(&self) -> &str {
        &self.manifest_hash
    }
    pub fn scopes(&self) -> &[Scope] {
        &self.scopes
    }
    pub fn market(&self) -> Result<&Runtime> {
        self.playback.market()
    }
    pub fn quotes(&self) -> Result<&crate::quote_state::Book> {
        self.playback.quotes()
    }
    pub fn pending(&self) -> Result<Option<Boundary<'_>>> {
        self.playback.pending()
    }
    pub fn status(&self) -> Status {
        self.playback.status()
    }
    pub fn pause(&mut self) -> Result<()> {
        self.playback.pause()
    }
    pub fn resume(&mut self) -> Result<()> {
        self.playback.resume()
    }
    pub fn step(&mut self) -> Result<()> {
        self.playback.step()
    }
    pub fn poll(&mut self) -> Result<Poll> {
        let result = self.playback.poll()?;
        if result == Poll::Boundary && self.barrier.is_none() {
            let boundary = self
                .playback
                .pending()?
                .ok_or_else(|| Error::Unready("playback boundary missing".into()))?;
            // Each account may provide its own feature hash. The barrier binds
            // the common source identity and clocks, not account-specific data.
            self.barrier = Some(Barrier::new(
                boundary.input(String::new()),
                &self.scopes,
                self.maximum_consumers,
            )?);
        }
        Ok(result)
    }
    pub fn needs_decision(&self, scope: &Scope) -> Result<bool> {
        self.barrier
            .as_ref()
            .ok_or_else(|| Error::Unready("no account boundary".into()))?
            .needs_decision(scope)
    }
    /// Advance the shared feature authority once per market boundary. Repeated
    /// calls while account journals are pending are idempotent.
    pub fn observe_features(
        &self,
        features: &mut crate::candidate_features::State,
    ) -> Result<bool> {
        let boundary = self
            .pending()?
            .ok_or_else(|| Error::Unready("no playback boundary".into()))?;
        features.observe(&boundary, self.market()?)
    }
    /// Use the same completed-candle evaluator as live. Quotes and account
    /// authorities remain explicit inputs; playback cannot infer their readiness.
    #[allow(clippy::too_many_arguments)]
    pub fn prepare_completed(
        &self,
        candidate: &mut crate::candidate_runtime::Runtime,
        features: &crate::candidate_features::State,
        context: crate::candidate_features::EntryContext<'_>,
        safety: &crate::strategy_dispatch::Safety,
        broker: &crate::strategy_candidate::PositionObservation,
        gates: &crate::strategy_adds::Gates,
        policy: &crate::strategy_candidate::Policy<'_>,
        intrabar: &crate::strategy_candidate::AcquisitionPolicy,
    ) -> Result<crate::strategy_dispatch::Decision> {
        if !self.needs_decision(candidate.scope())? {
            return Err(Error::Conflict(
                "playback consumer already committed".into(),
            ));
        }
        let boundary = self
            .pending()?
            .ok_or_else(|| Error::Unready("no playback boundary".into()))?;
        let frame = features.entry_frame(
            &boundary,
            boundary.evaluated_at_ns,
            self.market()?,
            self.quotes()?,
            context,
        )?;
        let decision = candidate.completed(
            boundary.input(String::new()),
            safety,
            &frame,
            broker,
            gates,
            policy,
            intrabar,
            features,
        )?;
        self.validate_decision(&decision)?;
        Ok(decision)
    }
    pub fn remaining(&self) -> Option<usize> {
        self.barrier.as_ref().map(Barrier::remaining)
    }
    #[allow(clippy::too_many_arguments)]
    pub fn prepare_intrabar(
        &self,
        candidate: &mut crate::candidate_runtime::Runtime,
        features: &crate::candidate_features::State,
        context: crate::candidate_features::AcquisitionContext,
        safety: &crate::strategy_dispatch::Safety,
        broker: &crate::strategy_candidate::PositionObservation,
        policy: &crate::strategy_candidate::Policy<'_>,
        intrabar: &crate::strategy_candidate::AcquisitionPolicy,
        recovery: &crate::strategy_lifecycle::RecoveryPolicy,
    ) -> Result<crate::strategy_dispatch::Decision> {
        if !self.needs_decision(candidate.scope())? {
            return Err(Error::Conflict(
                "playback consumer already committed".into(),
            ));
        }
        let boundary = self
            .pending()?
            .ok_or_else(|| Error::Unready("no playback boundary".into()))?;
        let (observation, body_high) =
            features.acquisition_frame(&boundary, self.market()?, self.quotes()?, context)?;
        let decision = candidate.intrabar(
            boundary.input(String::new()),
            safety,
            &observation,
            broker,
            body_high,
            policy,
            intrabar,
            features,
            recovery,
        )?;
        self.validate_decision(&decision)?;
        Ok(decision)
    }
    /// Check every prepared decision before any account writer performs I/O.
    pub fn validate_decision(&self, decision: &crate::strategy_dispatch::Decision) -> Result<()> {
        self.barrier
            .as_ref()
            .ok_or_else(|| Error::Unready("no account boundary".into()))?
            .validate_decision(decision)
    }
    pub fn record(&mut self, committed: &Committed) -> Result<bool> {
        self.barrier
            .as_mut()
            .ok_or_else(|| Error::Unready("no account boundary".into()))?
            .record(committed)
    }
    /// Cannot omit a declared consumer or advance on an uncommitted decision.
    pub fn acknowledge(&mut self) -> Result<()> {
        let input = self
            .playback
            .pending()?
            .ok_or_else(|| Error::Unready("no playback boundary".into()))?
            .input(String::new());
        let barrier = self
            .barrier
            .as_mut()
            .ok_or_else(|| Error::Unready("no account boundary".into()))?;
        barrier.acknowledge_market(&input, |id| self.playback.acknowledge(id))?;
        self.barrier = None;
        Ok(())
    }
}
