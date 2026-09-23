//! Immutable prepared-input catalog. Identity checks do not certify acquisition
//! coverage or establish that a modeled clock reproduces real execution latency.
use super::Prepared;
use crate::{
    content_hash,
    event_order::Scope,
    events::EventKey,
    run_manifest::{Clock, Pinned},
    Error, Result,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Shard {
    pub provider: u16,
    pub instrument: u64,
    pub session: u32,
    pub prepared_hash: String,
    pub clock_model: String,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Catalog {
    pub schema_version: u32,
    /// Independently certified source generation / recorded observations.
    pub authority_manifest_hash: String,
    pub clock: Clock,
    /// Sorted, unique (provider, instrument, session) keys.
    pub shards: Vec<Shard>,
}
/// Exact modeled event from a run-pinned historical prepared input. This does
/// not turn REST acquisition time into a measured live receive timestamp.
pub struct HistoricalEventProof {
    run_id: String,
    scope: Scope,
    key: EventKey,
    event_hash: String,
    source_time_ns: u64,
    modeled_available_at_ns: u64,
    evaluated_at_ns: u64,
    eligible: bool,
    prepared_hash: String,
    catalog_hash: String,
}
/// Bind the run catalog once; event lookup is then indexed and bounded.
pub struct HistoricalSource<'a> {
    prepared: &'a Prepared,
    catalog_hash: String,
    run_id: String,
}
impl HistoricalEventProof {
    pub fn run_id(&self) -> &str {
        &self.run_id
    }
    pub fn scope(&self) -> Scope {
        self.scope
    }
    pub fn key(&self) -> &EventKey {
        &self.key
    }
    pub fn event_hash(&self) -> &str {
        &self.event_hash
    }
    pub fn source_time_ns(&self) -> u64 {
        self.source_time_ns
    }
    pub fn modeled_available_at_ns(&self) -> u64 {
        self.modeled_available_at_ns
    }
    pub fn evaluated_at_ns(&self) -> u64 {
        self.evaluated_at_ns
    }
    pub fn eligible(&self) -> bool {
        self.eligible
    }
    pub fn identity_hash(&self) -> Result<String> {
        content_hash(&(
            "arte.historical-event-proof.v1",
            self.run_id.as_str(),
            self.prepared_hash.as_str(),
            self.catalog_hash.as_str(),
            &self.key,
            self.event_hash.as_str(),
            self.source_time_ns,
            self.modeled_available_at_ns,
            self.evaluated_at_ns,
            self.eligible,
        ))
    }
}
fn hash_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn key(shard: &Shard) -> (u16, u64, u32) {
    (shard.provider, shard.instrument, shard.session)
}
impl Catalog {
    pub fn bind_historical<'a>(
        &self,
        manifest: &Pinned,
        prepared: &'a Prepared,
    ) -> Result<HistoricalSource<'a>> {
        self.require(manifest, prepared)?;
        if self.clock != Clock::Historical {
            return Err(Error::Conflict(
                "historical event proof requires historical clock".into(),
            ));
        }
        Ok(HistoricalSource {
            prepared,
            catalog_hash: self.hash()?,
            run_id: manifest.manifest().run_id.clone(),
        })
    }
}
impl HistoricalSource<'_> {
    pub fn prepared(&self) -> &Prepared {
        self.prepared
    }
    /// Resolve the exact prepared frame/input without a source scan.
    pub fn event(&self, frame_index: usize, input_index: usize) -> Result<HistoricalEventProof> {
        let frame = self
            .prepared
            .frames
            .get(frame_index)
            .ok_or_else(|| Error::Unready("historical frame missing".into()))?;
        let input = frame
            .inputs
            .get(input_index)
            .ok_or_else(|| Error::Unready("historical frame input missing".into()))?;
        let event = &input.observation;
        if event.receipt.is_some()
            || event.key.kind != crate::events::EventKind::Trade
            || event.sip.ns > event.available_at_ns
            || event.available_at_ns > frame.evaluated_at_ns
        {
            return Err(Error::Conflict(
                "historical proof source or modeled clock differs".into(),
            ));
        }
        Ok(HistoricalEventProof {
            run_id: self.run_id.clone(),
            scope: self.prepared.scope,
            key: event.key.clone(),
            event_hash: content_hash(event)?,
            source_time_ns: event.sip.ns,
            modeled_available_at_ns: event.available_at_ns,
            evaluated_at_ns: frame.evaluated_at_ns,
            eligible: input.eligible,
            prepared_hash: self.prepared.hash.clone(),
            catalog_hash: self.catalog_hash.clone(),
        })
    }
}
impl Catalog {
    pub fn hash(&self) -> Result<String> {
        if self.schema_version != 1
            || !hash_valid(&self.authority_manifest_hash)
            || self.shards.is_empty()
            || self.shards.len() > 100_000
            || matches!(self.clock, Clock::Live)
            || matches!(&self.clock, Clock::FaultSimulation {scenario_hash} if !hash_valid(scenario_hash))
        {
            return Err(Error::Invalid("invalid playback source catalog".into()));
        }
        for shard in &self.shards {
            if shard.provider == 0
                || shard.instrument == 0
                || !(19000101..=29991231).contains(&shard.session)
                || !hash_valid(&shard.prepared_hash)
                || shard.clock_model.is_empty()
                || shard.clock_model.len() > 128
            {
                return Err(Error::Invalid("invalid prepared source shard".into()));
            }
        }
        if self
            .shards
            .windows(2)
            .any(|rows| key(&rows[0]) >= key(&rows[1]))
        {
            return Err(Error::Conflict(
                "source shards must be sorted and unique".into(),
            ));
        }
        content_hash(&("arte.playback-source-catalog.v1", self))
    }
    pub fn require(&self, manifest: &Pinned, prepared: &Prepared) -> Result<()> {
        if self.hash()? != manifest.manifest().source_manifest_hash
            || self.clock != manifest.manifest().clock
        {
            return Err(Error::Conflict(
                "run source catalog or clock differs".into(),
            ));
        }
        let scope = prepared.scope;
        let index = self
            .shards
            .binary_search_by_key(&(scope.provider, scope.instrument, scope.session), key)
            .map_err(|_| Error::Unready("prepared source shard is not declared".into()))?;
        let shard = &self.shards[index];
        if shard.prepared_hash != prepared.hash || shard.clock_model != prepared.clock_model {
            return Err(Error::Conflict(
                "prepared input or clock model differs".into(),
            ));
        }
        if self.clock == Clock::RecordedLive {
            require_recorded_receipts(prepared)?;
        }
        Ok(())
    }
}

/// Capture sequence is per run/lane, not per ticker. Gaps are valid in a ticker
/// projection. Multiple events may share one frame's monotonic receive time.
fn require_recorded_receipts(prepared: &Prepared) -> Result<()> {
    let mut lanes = BTreeMap::new();
    for input in prepared.frames.iter().flat_map(|frame| &frame.inputs) {
        let event = &input.observation;
        let receipt = event
            .receipt
            .as_ref()
            .ok_or_else(|| Error::Unready("recorded-live input lacks capture receipt".into()))?;
        if receipt.sequence == 0 || receipt.run_id.len() > 128 {
            return Err(Error::Invalid(
                "invalid recorded-live capture identity".into(),
            ));
        }
        let lane = (receipt.run_id.as_str(), receipt.lane);
        if !lanes.contains_key(&lane) && lanes.len() == 256 {
            return Err(Error::Capacity("recorded-live capture lane budget".into()));
        }
        if let Some(previous) = lanes.get(&lane) {
            let previous: &&crate::events::Observation = previous;
            let prior = previous.receipt.as_ref().unwrap();
            if receipt.sequence == prior.sequence {
                if event == *previous {
                    continue;
                }
                return Err(Error::Conflict(
                    "capture sequence reused for changed observation".into(),
                ));
            }
            if receipt.sequence < prior.sequence || receipt.monotonic_ns < prior.monotonic_ns {
                return Err(Error::Conflict(
                    "recorded-live capture ordering regressed".into(),
                ));
            }
        }
        lanes.insert(lane, event);
    }
    Ok(())
}
