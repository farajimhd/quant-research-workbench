//! Candidate component graph. A whole-run publication must pin this root with
//! controller and portfolio roots at the same boundary.
use super::*;
use arte_core::{candidate_config::Config, journal::Record, seed_storage::Object};
use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Root {
    version: u32,
    context: String,
    features: String,
    candidates: BTreeMap<String, String>,
}
pub struct Bundle {
    pub root: Object,
    pub features: Object,
    pub candidates: BTreeMap<String, Object>,
}
fn context(controller: &Runtime, maximum_bytes: usize) -> Result<String> {
    if maximum_bytes == 0 || maximum_bytes > 64 * 1024 * 1024 {
        return Err(Error::Capacity("candidate owner checkpoint budget".into()));
    }
    let run = controller.decision_view()?;
    let boundary = run
        .pending()?
        .ok_or_else(|| Error::Unready("candidate checkpoint boundary".into()))?;
    content_hash(&(
        "arte.candidate-owner.v1",
        run.manifest_hash(),
        boundary.id,
        boundary.sequence,
        boundary.evaluated_at_ns,
    ))
}
fn remaining(used: usize, maximum: usize) -> Result<usize> {
    maximum
        .checked_sub(used)
        .filter(|n| *n > 0)
        .ok_or_else(|| Error::Capacity("candidate graph byte budget".into()))
}
impl Candidates {
    pub fn checkpoint(&self, controller: &Runtime, maximum_bytes: usize) -> Result<Bundle> {
        self.require(controller)?;
        if self.slots.len() > 4096 || !self.configurations.keys().eq(self.slots.keys()) {
            return Err(Error::Unready(
                "candidate checkpoint requires bound policies".into(),
            ));
        }
        let context = context(controller, maximum_bytes)?;
        let run = controller.decision_view()?;
        let boundary = run.pending()?.unwrap();
        let features =
            self.features
                .checkpoint(&context, run.market()?, &boundary, maximum_bytes)?;
        let mut used = features.payload.len();
        let mut candidates = BTreeMap::new();
        let mut root = Root {
            version: 1,
            context,
            features: features.id.clone(),
            candidates: BTreeMap::new(),
        };
        for (key, slot) in &self.slots {
            let object = slot
                .runtime
                .checkpoint(&root.context, remaining(used, maximum_bytes)?)?;
            used += object.payload.len();
            root.candidates.insert(key.clone(), object.id.clone());
            candidates.insert(key.clone(), object);
        }
        // At most 4096 pairs of fixed-size hashes. Child payloads are not copied.
        let payload = serde_json::to_vec(&root).map_err(|e| Error::Serialization(e.to_string()))?;
        if payload.len() > remaining(used, maximum_bytes)? {
            return Err(Error::Capacity("candidate root byte budget".into()));
        }
        Ok(Bundle {
            root: Object::new(payload),
            features,
            candidates,
        })
    }
    /// Readbacks are independently selected LAST COMMITTED rows per scope, not
    /// a pending insert's rows. No journal write or market advancement occurs.
    pub fn restore_checkpoint(
        bundle: &Bundle,
        expected_root: &str,
        controller: &Runtime,
        configurations: BTreeMap<String, Config>,
        readbacks: &BTreeMap<String, Vec<Record>>,
        maximum_state_bytes: usize,
        maximum_bytes: usize,
    ) -> Result<Self> {
        let context = context(controller, maximum_bytes)?;
        if bundle.root.id != expected_root || bundle.candidates.len() > 4096 {
            return Err(Error::Conflict("candidate recovery root or count".into()));
        }
        let total = bundle
            .candidates
            .values()
            .fold(
                bundle
                    .root
                    .payload
                    .len()
                    .checked_add(bundle.features.payload.len()),
                |n, o| n.and_then(|n| n.checked_add(o.payload.len())),
            )
            .ok_or_else(|| Error::Capacity("candidate recovery size overflow".into()))?;
        if total > maximum_bytes {
            return Err(Error::Capacity("candidate recovery byte budget".into()));
        }
        bundle.root.verify()?;
        let root: Root = serde_json::from_slice(&bundle.root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if root.version != 1
            || root.context != context
            || root.features != bundle.features.id
            || !root.candidates.keys().eq(bundle.candidates.keys())
            || !root.candidates.keys().eq(configurations.keys())
            || !root.candidates.keys().eq(readbacks.keys())
            || serde_json::to_vec(&root).map_err(|e| Error::Serialization(e.to_string()))?
                != bundle.root.payload
        {
            return Err(Error::Conflict("candidate recovery graph differs".into()));
        }
        let run = controller.decision_view()?;
        if root.candidates.len() != run.scopes().len() {
            return Err(Error::Conflict("candidate recovery scopes differ".into()));
        }
        let config = configurations
            .values()
            .next()
            .ok_or_else(|| Error::Invalid("candidate recovery policies missing".into()))?;
        let boundary = run.pending()?.unwrap();
        let features = features::State::restore_checkpoint(
            &bundle.features,
            &root.features,
            &context,
            run.market()?,
            config.features.clone(),
            &boundary,
            maximum_bytes,
        )?;
        let mut slots = BTreeMap::new();
        for scope in run.scopes() {
            let key = content_hash(scope)?;
            let config = configurations
                .get(&key)
                .ok_or_else(|| Error::Conflict("candidate recovery policy scope".into()))?;
            if config.effective_hash(&features, run.quotes()?.policy_hash()?)? != scope.config_hash
            {
                return Err(Error::Conflict(
                    "candidate recovery effective policy differs".into(),
                ));
            }
            let (runtime, receipt) = Candidate::restore_checkpoint(
                &bundle.candidates[&key],
                &root.candidates[&key],
                &context,
                scope,
                maximum_state_bytes,
                maximum_bytes,
                &readbacks[&key],
            )?;
            if let Some(batch) = runtime.pending_batch() {
                if batch.records().len() != 1 {
                    return Err(Error::Invalid(
                        "recovered candidate pending row count".into(),
                    ));
                }
                run.validate_decision(&batch.records()[0].decode()?)?;
            }
            let current = receipt
                .as_ref()
                .filter(|r| r.decision().input.event_id == boundary.id);
            if receipt.as_ref().is_some_and(|r| {
                let input = &r.decision().input;
                input.source_sequence > boundary.sequence
                    || input.evaluated_at_ns > boundary.evaluated_at_ns
                    || (input.source_sequence == boundary.sequence && input.event_id != boundary.id)
            }) {
                return Err(Error::Conflict(
                    "candidate receipt exceeds recovery boundary".into(),
                ));
            }
            if let Some(receipt) = current {
                run.validate_decision(receipt.decision())?;
            }
            if !run.needs_decision(scope)? && current.is_none() {
                return Err(Error::Conflict(
                    "controller receipt missing from candidate state".into(),
                ));
            }
            slots.insert(key, Slot { runtime, receipt });
        }
        let restored = Self {
            manifest_hash: run.manifest_hash().into(),
            features,
            slots,
            configurations,
        };
        restored.require(controller)?;
        Ok(restored)
    }
}
