//! One pending scheduler cut joins market, features, and exact signal state.
//! Restored quote/LULD safety and release frontiers begin empty. They require
//! new live observations and policy binding before exposure can resume.
use super::Lane;
use crate::live_exact_signal;
use arte_core::{
    candidate_features,
    market_structure::scheduler::{self, Scheduler},
    seed_storage::Object,
    Error, Result,
};
use serde::{Deserialize, Serialize};

pub struct Bundle {
    pub root: Object,
    pub scheduler: scheduler::checkpoint::Bundle,
    pub features: Object,
    pub signal: live_exact_signal::Bundle,
}
pub struct References {
    pub scheduler: String,
    pub features: String,
    pub signal: String,
    pub sequence: u64,
    pub evaluated_at_ns: u64,
}
impl Bundle {
    pub fn references(root: &Object) -> Result<References> {
        root.verify()?;
        if root.payload.len() > 4096 {
            return Err(Error::Capacity("live cut root bytes".into()));
        }
        let saved: Root = serde_json::from_slice(&root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if saved.version != 1
            || !valid_hash(&saved.context_hash)
            || !valid_hash(&saved.scheduler)
            || !valid_hash(&saved.features)
            || !valid_hash(&saved.signal)
            || saved.sequence == 0
            || saved.boundary_id.is_empty()
            || saved.evaluated_at_ns == 0
        {
            return Err(Error::Invalid("live cut references".into()));
        }
        Ok(References {
            scheduler: saved.scheduler,
            features: saved.features,
            signal: saved.signal,
            sequence: saved.sequence,
            evaluated_at_ns: saved.evaluated_at_ns,
        })
    }
    pub fn objects(&self) -> [&Object; 13] {
        [
            &self.scheduler.market,
            &self.scheduler.trades,
            &self.scheduler.quotes,
            &self.scheduler.book,
            &self.scheduler.root,
            &self.features,
            &self.signal.bars,
            &self.signal.signal,
            &self.signal.noise,
            &self.signal.macd,
            &self.signal.macd_source,
            &self.signal.root,
            &self.root,
        ]
    }
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Root {
    version: u32,
    context_hash: String,
    scope: (u16, u64, u32),
    sequence: u64,
    boundary_id: String,
    evaluated_at_ns: u64,
    allowed_lateness_ns: u64,
    maximum_quote_age_ns: u64,
    scheduler: String,
    features: String,
    signal: String,
}
pub struct Request<'a> {
    pub context_hash: &'a str,
    pub expected_root: &'a str,
    pub scheduler: scheduler::checkpoint::Request<'a>,
    pub signal: live_exact_signal::Recovery<'a>,
    pub feature_config: candidate_features::Config,
    pub allowed_lateness_ns: u64,
    pub maximum_quote_age_ns: u64,
    pub maximum_bytes: usize,
}
fn valid_hash(hash: &str) -> bool {
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn total(bundle: &Bundle) -> Result<usize> {
    bundle
        .objects()
        .iter()
        .try_fold(0usize, |sum, object| sum.checked_add(object.payload.len()))
        .ok_or_else(|| Error::Capacity("live cut bytes overflow".into()))
}
fn root_object(root: &Root) -> Result<Object> {
    let bytes = serde_json::to_vec(root).map_err(|e| Error::Serialization(e.to_string()))?;
    if bytes.len() > 4096 {
        return Err(Error::Capacity("live cut root bytes".into()));
    }
    Ok(Object::new(bytes))
}
impl Lane {
    /// Capture only after every feature and the exact signal have consumed the
    /// same pending boundary. Acknowledged cuts cannot be reconstructed here.
    pub fn checkpoint_pending(&self, context_hash: &str, maximum_bytes: usize) -> Result<Bundle> {
        self.available()?;
        if !valid_hash(context_hash) || maximum_bytes == 0 || maximum_bytes > 64 * 1024 * 1024 {
            return Err(Error::Invalid("live cut context or budget".into()));
        }
        let pending = self
            .market
            .pending()?
            .ok_or_else(|| Error::Unready("live cut requires pending boundary".into()))?;
        let owner = self
            .exact_signal
            .as_ref()
            .ok_or_else(|| Error::Unready("live cut exact signal missing".into()))?;
        if owner.last_boundary() != (pending.sequence, Some(pending.id))
            || owner.last_evaluated_at_ns() != pending.evaluated_at_ns
        {
            return Err(Error::Conflict("live cut signal boundary differs".into()));
        }
        let features = self.features.checkpoint(
            context_hash,
            self.market.state()?,
            &pending,
            maximum_bytes,
        )?;
        let scheduler = self.market.checkpoint(context_hash, maximum_bytes)?;
        let signal = owner.checkpoint()?;
        let scope = self.market.scope();
        let root = root_object(&Root {
            version: 1,
            context_hash: context_hash.into(),
            scope: (scope.provider, scope.instrument, scope.session),
            sequence: pending.sequence,
            boundary_id: pending.id.into(),
            evaluated_at_ns: pending.evaluated_at_ns,
            allowed_lateness_ns: self.allowed_lateness_ns,
            maximum_quote_age_ns: self.maximum_quote_age_ns,
            scheduler: scheduler.root.id.clone(),
            features: features.id.clone(),
            signal: signal.root.id.clone(),
        })?;
        let bundle = Bundle {
            root,
            scheduler,
            features,
            signal,
        };
        if total(&bundle)? > maximum_bytes {
            return Err(Error::Capacity("live cut component budget".into()));
        }
        Ok(bundle)
    }
    pub fn restore_pending(bundle: &Bundle, request: Request<'_>) -> Result<Self> {
        if !valid_hash(request.context_hash)
            || request.context_hash != request.scheduler.context_hash
            || bundle.root.id != request.expected_root
            || bundle.root.payload.len() > 4096
            || request.maximum_bytes == 0
            || request.maximum_bytes > 64 * 1024 * 1024
            || total(bundle)? > request.maximum_bytes
        {
            return Err(Error::Invalid("live cut recovery root or budget".into()));
        }
        bundle.root.verify()?;
        let root: Root = serde_json::from_slice(&bundle.root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if root.version != 1
            || root.context_hash != request.context_hash
            || root.scheduler != bundle.scheduler.root.id
            || root.features != bundle.features.id
            || root.signal != bundle.signal.root.id
            || root.allowed_lateness_ns != request.allowed_lateness_ns
            || root.maximum_quote_age_ns != request.maximum_quote_age_ns
            || root.sequence != request.signal.expected_sequence
            || root.boundary_id != request.signal.expected_boundary_id.unwrap_or_default()
            || root_object(&root)?.payload != bundle.root.payload
        {
            return Err(Error::Conflict("live cut recovery pins differ".into()));
        }
        let scheduler = Scheduler::restore_checkpoint(
            &bundle.scheduler,
            &bundle.scheduler.root.id,
            request.scheduler,
        )?;
        let scope = scheduler.scope();
        let pending = scheduler
            .pending()?
            .ok_or_else(|| Error::Unready("live cut pending boundary missing".into()))?;
        if root.scope != (scope.provider, scope.instrument, scope.session)
            || root.sequence != pending.sequence
            || root.boundary_id != pending.id
            || root.evaluated_at_ns != pending.evaluated_at_ns
        {
            return Err(Error::Conflict(
                "live cut scheduler boundary differs".into(),
            ));
        }
        let features = candidate_features::State::restore_checkpoint(
            &bundle.features,
            &bundle.features.id,
            request.context_hash,
            scheduler.state()?,
            request.feature_config.clone(),
            &pending,
            request.maximum_bytes,
        )?;
        let signal = live_exact_signal::Owner::restore(
            &bundle.signal,
            &bundle.signal.root.id,
            request.signal,
        )?;
        let mut lane = Self::new(
            scheduler,
            request.allowed_lateness_ns,
            request.maximum_quote_age_ns,
            request.feature_config,
        )?;
        lane.bind_recovered_exact_signal(signal)?;
        lane.features = features;
        Ok(lane)
    }
}
