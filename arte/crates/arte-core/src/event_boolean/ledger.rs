//! Playback-certified source ledger for event-cadence calculations.
//! The Boolean producer does not get to certify its own omitted inputs.
use super::{source_domain, source_item};
use crate::{
    coverage::Interval,
    event_order::Scope,
    execution_interval::{ExecutionContract, ExecutionInterval, Route},
    market_structure::scheduler::{
        playback::{sources::Catalog, Mode, Playback, Prepared},
        Boundary, Kind,
    },
    run_manifest::Pinned,
    Error, Result,
};
use sha2::{Digest, Sha256};

pub struct SourceLedger {
    scope: Scope,
    interval: Interval,
    route: Route,
    definition_hash: String,
    maximum_events: u64,
    count: u64,
    last_sequence: u64,
    last_source_ns: u64,
    last_evaluated_ns: u64,
    digest: Sha256,
}

/// Constructed only after the shared playback is complete and its prepared
/// source is pinned by the run manifest and source catalogue.
pub struct SourceProof {
    scope: Scope,
    interval: Interval,
    definition_hash: String,
    authority_hash: String,
    event_count: u64,
    source_hash: String,
    certified_at_ns: u64,
}
impl SourceProof {
    pub fn scope(&self) -> Scope {
        self.scope
    }
    pub fn interval(&self) -> Interval {
        self.interval
    }
    pub fn definition_hash(&self) -> &str {
        &self.definition_hash
    }
    pub fn authority_hash(&self) -> &str {
        &self.authority_hash
    }
    pub fn event_count(&self) -> u64 {
        self.event_count
    }
    pub fn source_hash(&self) -> &str {
        &self.source_hash
    }
    pub fn certified_at_ns(&self) -> u64 {
        self.certified_at_ns
    }
}

impl SourceLedger {
    pub fn new(
        scope: Scope,
        interval: Interval,
        definition: &ExecutionContract,
        maximum_events: u64,
    ) -> Result<Self> {
        crate::event_order::Buffer::new(scope, 1, 0)?;
        interval.validate()?;
        let route = Route::new(definition)?;
        if route.interval() != ExecutionInterval::Events
            || maximum_events == 0
            || maximum_events > 100_000_000
        {
            return Err(Error::Invalid("event source ledger clock or budget".into()));
        }
        let definition_hash = definition.hash()?;
        let domain = source_domain(scope, interval, &definition_hash)?;
        let mut digest = Sha256::new();
        digest.update(b"arte.event-boolean.source.v1");
        digest.update(domain.as_bytes());
        Ok(Self {
            scope,
            interval,
            route,
            definition_hash,
            maximum_events,
            count: 0,
            last_sequence: 0,
            last_source_ns: 0,
            last_evaluated_ns: 0,
            digest,
        })
    }

    /// Invoke on every shared playback boundary. Completed bars are outside
    /// this event clock; every admitted trade or quote must be observed once.
    pub fn observe(&mut self, boundary: &Boundary<'_>) -> Result<bool> {
        if matches!(boundary.kind, Kind::Completed { .. }) {
            return Ok(false);
        }
        if !boundary.due_for(self.route) {
            return Err(Error::Conflict("event source ledger route".into()));
        }
        if self.count == self.maximum_events {
            return Err(Error::Capacity("event source ledger budget".into()));
        }
        let (item, source_ns) = source_item(
            boundary,
            self.scope,
            self.interval,
            self.last_sequence,
            self.last_source_ns,
            self.last_evaluated_ns,
        )?;
        self.digest.update(item.as_bytes());
        self.count += 1;
        self.last_sequence = boundary.sequence;
        self.last_source_ns = source_ns;
        self.last_evaluated_ns = boundary.evaluated_at_ns;
        Ok(true)
    }

    pub fn certify_playback(
        self,
        playback: &Playback,
        prepared: &Prepared,
        catalog: &Catalog,
        manifest: &Pinned,
        certified_at_ns: u64,
    ) -> Result<SourceProof> {
        catalog.require(manifest, prepared)?;
        prepared.require_interval(self.interval)?;
        let status = playback.status();
        if self.scope != prepared.scope()
            || self.scope != playback.scope()
            || playback.run_id() != manifest.manifest().run_id
            || status.mode != Mode::Complete
            || status.prepared_hash != prepared.hash()
            || status.completed_frames != status.total_frames
            || status.pending_boundary
            || status.failure.is_some()
            || status.queued_events != 0
            || status.total_events != status.admitted_events + status.coalesced_events
            || self.count != status.admitted_events as u64
            || status.acknowledged_boundaries < self.count
            || certified_at_ns < self.interval.end
        {
            return Err(Error::Unready(
                "event source playback not fully certified".into(),
            ));
        }
        Ok(SourceProof {
            scope: self.scope,
            interval: self.interval,
            definition_hash: self.definition_hash,
            authority_hash: catalog.hash()?,
            event_count: self.count,
            source_hash: format!("{:x}", self.digest.finalize()),
            certified_at_ns,
        })
    }
}
