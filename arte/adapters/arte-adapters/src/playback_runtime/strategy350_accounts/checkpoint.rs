//! Strategy 350 account-state graph at one dispatched market boundary.
//! A whole-run publisher must bind this root to all market and portfolio roots.
use super::*;
use arte_core::{journal::Record, portfolio::checkpoint::Cut, seed_storage::Object};
use serde::{de::DeserializeOwned, Deserialize};

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Root {
    version: u32,
    mode: Mode,
    global_cut: Option<Cut>,
    context: String,
    configurations: BTreeMap<String, String>,
    accounts: BTreeMap<String, String>,
}
#[derive(Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Mode {
    Selected,
    Standby,
}

pub struct Bundle {
    pub root: Object,
    pub accounts: BTreeMap<String, Object>,
}

fn context(controller: &Playback, cut: Option<&Cut>, maximum_bytes: usize) -> Result<String> {
    if maximum_bytes == 0 || maximum_bytes > 64 * 1024 * 1024 {
        return Err(Error::Capacity(
            "Strategy 350 owner checkpoint budget".into(),
        ));
    }
    let run = if cut.is_some() {
        &controller.run
    } else {
        controller.decision_view()?
    };
    let boundary = run
        .pending()?
        .ok_or_else(|| Error::Unready("Strategy 350 checkpoint boundary".into()))?;
    if let Some(cut) = cut {
        if boundary.evaluated_at_ns < cut.at_ns
            || run.status().acknowledged_boundaries.checked_add(1) != Some(boundary.sequence)
            || run.manifest_hash() != controller.manifest_hash()
        {
            return Err(Error::Conflict(
                "Strategy 350 standby boundary differs".into(),
            ));
        }
        content_hash(&(
            "arte.strategy-350-standby-owner.v1",
            run.manifest_hash(),
            cut,
            run.market_scope().provider,
            run.market_scope().instrument,
            run.market_scope().session,
            boundary.id,
            boundary.sequence,
        ))
    } else {
        content_hash(&(
            "arte.strategy-350-account-owner.v1",
            run.manifest_hash(),
            boundary.id,
            boundary.sequence,
            boundary.evaluated_at_ns,
        ))
    }
}

impl<S: StateContract> Accounts<S> {
    pub fn checkpoint(&self, controller: &Playback, maximum_bytes: usize) -> Result<Bundle> {
        self.checkpoint_with_cut(controller, None, maximum_bytes)
    }

    pub fn checkpoint_standby(
        &self,
        controller: &Playback,
        global_cut: &Cut,
        maximum_bytes: usize,
    ) -> Result<Bundle> {
        self.checkpoint_with_cut(controller, Some(global_cut), maximum_bytes)
    }

    fn checkpoint_with_cut(
        &self,
        controller: &Playback,
        cut: Option<&Cut>,
        maximum_bytes: usize,
    ) -> Result<Bundle> {
        self.require_controller(controller)?;
        let context = context(controller, cut, maximum_bytes)?;
        if self.slots.len() > 4096 {
            return Err(Error::Capacity("Strategy 350 owner scope budget".into()));
        }
        let run = if cut.is_some() {
            &controller.run
        } else {
            controller.decision_view()?
        };
        let boundary = run.pending()?.unwrap();
        let mut used = 0usize;
        let mut root = Root {
            version: 2,
            mode: if cut.is_some() {
                Mode::Standby
            } else {
                Mode::Selected
            },
            global_cut: cut.cloned(),
            context,
            configurations: BTreeMap::new(),
            accounts: BTreeMap::new(),
        };
        let mut accounts = BTreeMap::new();
        for (key, slot) in &self.slots {
            slot.runtime
                .committed_state()
                .validate_for(&slot.effective)?;
            let due = run.is_due(slot.runtime.scope())?;
            let needs = run.needs_decision(slot.runtime.scope())?;
            let current = slot
                .receipt
                .as_ref()
                .filter(|receipt| receipt.decision().input.event_id == boundary.id);
            if (cut.is_some() && (!needs && due || current.is_some()))
                || (cut.is_none()
                    && ((due && !needs && current.is_none())
                        || (needs && current.is_some())
                        || (!due && current.is_some())))
            {
                return Err(Error::Conflict(
                    "Strategy 350 owner receipt and barrier differ".into(),
                ));
            }
            if let Some(batch) = slot.runtime.pending_batch() {
                if cut.is_some() {
                    return Err(Error::Conflict(
                        "Strategy 350 standby has pending decision".into(),
                    ));
                }
                if batch.records().len() != 1 {
                    return Err(Error::Invalid("Strategy 350 pending row count".into()));
                }
                run.validate_decision(&batch.records()[0].decode()?)?;
            }
            if let Some(receipt) = current {
                run.validate_decision(receipt.decision())?;
            }
            if let Some(receipt) = &slot.receipt {
                let input = &receipt.decision().input;
                if (cut.is_some()
                    && (input.source_sequence > run.status().acknowledged_boundaries
                        || input.evaluated_at_ns > cut.unwrap().at_ns))
                    || (cut.is_none()
                        && (input.source_sequence > boundary.sequence
                            || input.evaluated_at_ns > boundary.evaluated_at_ns
                            || (input.source_sequence == boundary.sequence
                                && input.event_id != boundary.id)))
                {
                    return Err(Error::Conflict(
                        "Strategy 350 receipt exceeds checkpoint boundary".into(),
                    ));
                }
            }
            let left = maximum_bytes
                .checked_sub(used)
                .filter(|left| *left > 0)
                .ok_or_else(|| Error::Capacity("Strategy 350 owner byte budget".into()))?;
            let object = slot.runtime.checkpoint(&root.context, left)?;
            used = used
                .checked_add(object.payload.len())
                .ok_or_else(|| Error::Capacity("Strategy 350 owner size overflow".into()))?;
            root.configurations
                .insert(key.clone(), slot.effective.hash()?);
            root.accounts.insert(key.clone(), object.id.clone());
            accounts.insert(key.clone(), object);
        }
        let payload = serde_json::to_vec(&root).map_err(|e| Error::Serialization(e.to_string()))?;
        if payload.len() > maximum_bytes.saturating_sub(used) {
            return Err(Error::Capacity("Strategy 350 owner root budget".into()));
        }
        Ok(Bundle {
            root: Object::new(payload),
            accounts,
        })
    }
}

impl<S: StateContract + DeserializeOwned> Accounts<S> {
    pub fn restore_checkpoint(
        bundle: &Bundle,
        expected_root: &str,
        controller: &Playback,
        configurations: BTreeMap<String, Config>,
        readbacks: &BTreeMap<String, Vec<Record>>,
        maximum_state_bytes: usize,
        maximum_bytes: usize,
    ) -> Result<Self> {
        Self::restore_with_cut(
            bundle,
            expected_root,
            controller,
            None,
            configurations,
            readbacks,
            maximum_state_bytes,
            maximum_bytes,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn restore_standby_checkpoint(
        bundle: &Bundle,
        expected_root: &str,
        controller: &Playback,
        global_cut: &Cut,
        configurations: BTreeMap<String, Config>,
        readbacks: &BTreeMap<String, Vec<Record>>,
        maximum_state_bytes: usize,
        maximum_bytes: usize,
    ) -> Result<Self> {
        Self::restore_with_cut(
            bundle,
            expected_root,
            controller,
            Some(global_cut),
            configurations,
            readbacks,
            maximum_state_bytes,
            maximum_bytes,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn restore_with_cut(
        bundle: &Bundle,
        expected_root: &str,
        controller: &Playback,
        cut: Option<&Cut>,
        mut configurations: BTreeMap<String, Config>,
        readbacks: &BTreeMap<String, Vec<Record>>,
        maximum_state_bytes: usize,
        maximum_bytes: usize,
    ) -> Result<Self> {
        let context = context(controller, cut, maximum_bytes)?;
        if bundle.root.id != expected_root || bundle.accounts.len() > 4096 {
            return Err(Error::Conflict("Strategy 350 owner root or count".into()));
        }
        let total = bundle
            .accounts
            .values()
            .try_fold(bundle.root.payload.len(), |used, object| {
                used.checked_add(object.payload.len())
            })
            .ok_or_else(|| Error::Capacity("Strategy 350 owner size overflow".into()))?;
        if total > maximum_bytes {
            return Err(Error::Capacity("Strategy 350 owner byte budget".into()));
        }
        bundle.root.verify()?;
        let root: Root = serde_json::from_slice(&bundle.root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if root.version != 2
            || root.mode
                != if cut.is_some() {
                    Mode::Standby
                } else {
                    Mode::Selected
                }
            || root.global_cut.as_ref() != cut
            || root.context != context
            || !root.accounts.keys().eq(bundle.accounts.keys())
            || !root.accounts.keys().eq(root.configurations.keys())
            || !root.accounts.keys().eq(configurations.keys())
            || !root.accounts.keys().eq(readbacks.keys())
            || serde_json::to_vec(&root).map_err(|e| Error::Serialization(e.to_string()))?
                != bundle.root.payload
        {
            return Err(Error::Conflict("Strategy 350 owner graph differs".into()));
        }
        let run = if cut.is_some() {
            &controller.run
        } else {
            controller.decision_view()?
        };
        if run.scopes().len() != root.accounts.len() {
            return Err(Error::Conflict("Strategy 350 owner scopes differ".into()));
        }
        let boundary = run.pending()?.unwrap();
        let mut slots = BTreeMap::new();
        for scope in run.scopes() {
            let key = content_hash(scope)?;
            let effective = configurations.remove(&key).ok_or_else(|| {
                Error::Conflict("Strategy 350 owner configuration missing".into())
            })?;
            let object = bundle.accounts.get(&key).ok_or_else(|| {
                Error::Conflict("Strategy 350 owner account image missing".into())
            })?;
            let image_hash = root
                .accounts
                .get(&key)
                .ok_or_else(|| Error::Conflict("Strategy 350 owner account pin missing".into()))?;
            let rows = readbacks.get(&key).ok_or_else(|| {
                Error::Conflict("Strategy 350 owner journal readback missing".into())
            })?;
            effective.require_scope(scope)?;
            if root.configurations.get(&key) != Some(&effective.hash()?) || image_hash != &object.id
            {
                return Err(Error::Conflict("Strategy 350 owner pins differ".into()));
            }
            let route = Route::new(&ExecutionContract {
                kind: ExecutableKind::Strategy,
                id: scope.strategy_instance.clone(),
                implementation_hash: scope.config_hash.clone(),
                interval: effective.execution_interval,
            })?;
            let (runtime, receipt) = Runtime::<S>::restore_checkpoint(
                object,
                image_hash,
                &root.context,
                scope,
                maximum_state_bytes,
                maximum_bytes,
                rows,
            )?;
            runtime.committed_state().validate_for(&effective)?;
            if let Some(batch) = runtime.pending_batch() {
                if cut.is_some() {
                    return Err(Error::Conflict(
                        "Strategy 350 standby has pending decision".into(),
                    ));
                }
                if batch.records().len() != 1 {
                    return Err(Error::Invalid("Strategy 350 pending row count".into()));
                }
                run.validate_decision(&batch.records()[0].decode()?)?;
            }
            if receipt.as_ref().is_some_and(|receipt| {
                let input = &receipt.decision().input;
                (cut.is_some()
                    && (input.source_sequence > run.status().acknowledged_boundaries
                        || input.evaluated_at_ns > cut.unwrap().at_ns))
                    || (cut.is_none()
                        && (input.source_sequence > boundary.sequence
                            || input.evaluated_at_ns > boundary.evaluated_at_ns
                            || (input.source_sequence == boundary.sequence
                                && input.event_id != boundary.id)))
            }) {
                return Err(Error::Conflict(
                    "Strategy 350 receipt exceeds recovery boundary".into(),
                ));
            }
            let current = receipt
                .as_ref()
                .filter(|receipt| receipt.decision().input.event_id == boundary.id);
            if let Some(receipt) = current {
                run.validate_decision(receipt.decision())?;
            }
            let due = run.is_due(scope)?;
            let needs = run.needs_decision(scope)?;
            if (cut.is_some() && (!needs && due || current.is_some()))
                || (cut.is_none()
                    && ((due && !needs && current.is_none())
                        || (needs && current.is_some())
                        || (!due && current.is_some())))
            {
                return Err(Error::Conflict(
                    "Strategy 350 owner receipt and barrier differ".into(),
                ));
            }
            slots.insert(
                key,
                Slot {
                    runtime,
                    effective,
                    route,
                    receipt,
                },
            );
        }
        let restored = Self {
            manifest_hash: run.manifest_hash().into(),
            slots,
        };
        restored.require_controller(controller)?;
        if restored
            .checkpoint_with_cut(controller, cut, maximum_bytes)?
            .root
            .id
            != expected_root
        {
            return Err(Error::Conflict(
                "Strategy 350 owner image is not canonical".into(),
            ));
        }
        Ok(restored)
    }
}
