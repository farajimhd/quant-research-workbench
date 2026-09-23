//! Coordinated controller capture at a dispatched boundary with durable fills.
//! This graph is not yet whole-run recovery: candidate and portfolio are separate.
use super::*;
use arte_core::{
    content_hash, execution_events::Fill, portfolio::checkpoint::Cut, run_manifest::Pinned,
    seed_storage::Object,
};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, io::Write};

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ActionProgress {
    pub decision_id: String,
    pub action_index: usize,
    pub decision_hash: String,
    pub completed_request: Option<String>,
    pub reserved_request: Option<String>,
    pub allocation: Option<arte_core::decision_orders::Allocation>,
    pub rejection: Option<RejectionProgress>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct RejectionProgress {
    pub record: arte_core::action_rejection::Record,
    pub journaled: bool,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Root {
    version: u32,
    mode: ImageMode,
    manifest_hash: String,
    cut: Cut,
    local_head: String,
    local_sequence: u64,
    playback: String,
    execution: String,
    maximum_quote_age_ns: u64,
    actions: Vec<ActionProgress>,
    targets: BTreeMap<String, super::candidate_position::TargetRecord>,
}
#[derive(Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum ImageMode {
    Selected,
    Standby,
}
pub struct Bundle {
    pub root: Object,
    pub playback: arte_core::market_structure::scheduler::playback::accounts::checkpoint::Bundle,
    pub execution: simulation_runtime::checkpoint::Bundle,
}
struct Writer {
    bytes: Vec<u8>,
    maximum: usize,
}
impl Write for Writer {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if bytes.len() > self.maximum.saturating_sub(self.bytes.len()) {
            return Err(std::io::Error::other(
                "playback controller recovery byte budget",
            ));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
impl Runtime {
    /// expected_root must come from a trusted durable publication, not the image
    /// being supplied. Restore never sends orders or advances market input.
    #[allow(clippy::too_many_arguments)]
    pub fn restore_checkpoint(
        bundle: &Bundle,
        expected_root: &str,
        manifest: &Pinned,
        cut: &Cut,
        sources: &arte_core::market_structure::scheduler::playback::sources::Catalog,
        prepared: arte_core::market_structure::scheduler::playback::Prepared,
        market_request: arte_core::market_structure::scheduler::checkpoint::Request<'_>,
        frames_per_poll: usize,
        maximum_consumers: usize,
        receipts: &[&Committed],
        costs: arte_core::simulation_costs::Pinned,
        execution_limits: simulation_runtime::checkpoint::Limits,
        maximum_bytes: usize,
    ) -> Result<Self> {
        Self::restore_image(
            bundle,
            expected_root,
            manifest,
            cut,
            sources,
            prepared,
            market_request,
            frames_per_poll,
            maximum_consumers,
            receipts,
            costs,
            execution_limits,
            maximum_bytes,
            ImageMode::Selected,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn restore_standby_checkpoint(
        bundle: &Bundle,
        expected_root: &str,
        manifest: &Pinned,
        global_cut: &Cut,
        sources: &arte_core::market_structure::scheduler::playback::sources::Catalog,
        prepared: arte_core::market_structure::scheduler::playback::Prepared,
        market_request: arte_core::market_structure::scheduler::checkpoint::Request<'_>,
        frames_per_poll: usize,
        maximum_consumers: usize,
        costs: arte_core::simulation_costs::Pinned,
        execution_limits: simulation_runtime::checkpoint::Limits,
        maximum_bytes: usize,
    ) -> Result<Self> {
        Self::restore_image(
            bundle,
            expected_root,
            manifest,
            global_cut,
            sources,
            prepared,
            market_request,
            frames_per_poll,
            maximum_consumers,
            &[],
            costs,
            execution_limits,
            maximum_bytes,
            ImageMode::Standby,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn restore_image(
        bundle: &Bundle,
        expected_root: &str,
        manifest: &Pinned,
        cut: &Cut,
        sources: &arte_core::market_structure::scheduler::playback::sources::Catalog,
        prepared: arte_core::market_structure::scheduler::playback::Prepared,
        market_request: arte_core::market_structure::scheduler::checkpoint::Request<'_>,
        frames_per_poll: usize,
        maximum_consumers: usize,
        receipts: &[&Committed],
        costs: arte_core::simulation_costs::Pinned,
        execution_limits: simulation_runtime::checkpoint::Limits,
        maximum_bytes: usize,
        mode: ImageMode,
    ) -> Result<Self> {
        if maximum_bytes == 0 || maximum_bytes > 64 * 1024 * 1024 || bundle.root.id != expected_root
        {
            return Err(Error::Invalid(
                "controller recovery identity or budget".into(),
            ));
        }
        let scheduler = &bundle.playback.playback.scheduler;
        let total = [
            &bundle.root,
            &bundle.execution.root,
            &bundle.playback.root,
            &bundle.playback.playback.root,
            &scheduler.root,
            &scheduler.market,
            &scheduler.trades,
            &scheduler.quotes,
            &scheduler.book,
        ]
        .into_iter()
        .chain(bundle.playback.barrier.iter())
        .chain(bundle.execution.objects.values())
        .try_fold(0usize, |n, o| n.checked_add(o.payload.len()))
        .ok_or_else(|| Error::Capacity("controller recovery size overflow".into()))?;
        if total > maximum_bytes {
            return Err(Error::Capacity("controller recovery total bytes".into()));
        }
        bundle.root.verify()?;
        let root: Root = serde_json::from_slice(&bundle.root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        let scope = prepared.scope();
        let context = match mode {
            ImageMode::Selected => {
                content_hash(&("arte.playback-controller-cut.v1", manifest.hash(), cut))?
            }
            ImageMode::Standby => content_hash(&(
                "arte.playback-controller-standby.v1",
                manifest.hash(),
                cut,
                scope.provider,
                scope.instrument,
                scope.session,
                root.local_head.as_str(),
                root.local_sequence,
            ))?,
        };
        if root.version != 6
            || root.mode != mode
            || (mode == ImageMode::Selected
                && (root.local_head != cut.boundary_hash
                    || root.local_sequence != cut.boundary_sequence))
            || (mode == ImageMode::Standby && !root.actions.is_empty())
            || root.manifest_hash != manifest.hash()
            || root.cut != *cut
            || root.playback != bundle.playback.root.id
            || root.execution != bundle.execution.root.id
            || market_request.context_hash != context
            || serde_json::to_vec(&root).map_err(|e| Error::Serialization(e.to_string()))?
                != bundle.root.payload
        {
            return Err(Error::Conflict("controller recovery pins differ".into()));
        }
        let run = Run::restore_checkpoint(
            &bundle.playback,
            &root.playback,
            manifest,
            sources,
            prepared,
            market_request,
            frames_per_poll,
            maximum_consumers,
            receipts,
        )?;
        let boundary = run
            .pending()?
            .ok_or_else(|| Error::Unready("controller recovery boundary missing".into()))?;
        if boundary.id != root.local_head
            || boundary.sequence != root.local_sequence
            || (mode == ImageMode::Selected
                && (boundary.id != cut.boundary_hash
                    || boundary.evaluated_at_ns != cut.at_ns
                    || boundary.sequence != cut.boundary_sequence))
            || (mode == ImageMode::Standby && boundary.evaluated_at_ns < cut.at_ns)
            || run.status().acknowledged_boundaries.checked_add(1) != Some(boundary.sequence)
        {
            return Err(Error::Conflict("controller recovery cut differs".into()));
        }
        let execution = match mode {
            ImageMode::Selected => simulation_runtime::Runtime::restore_checkpoint(
                &bundle.execution,
                &root.execution,
                manifest,
                cut,
                costs,
                execution_limits,
            )?,
            ImageMode::Standby => simulation_runtime::Runtime::restore_standby_checkpoint(
                &bundle.execution,
                &root.execution,
                manifest,
                cut,
                &run,
                costs,
                execution_limits,
            )?,
        };
        execution.require_recovered_playback(&run, root.maximum_quote_age_ns)?;
        let actions = actions::Work::restore(&root.actions, receipts)?;
        let restored = Self {
            run,
            execution,
            maximum_quote_age_ns: root.maximum_quote_age_ns,
            dispatched_boundary: (mode == ImageMode::Selected).then(|| cut.boundary_hash.clone()),
            actions,
            targets: root.targets,
            startup_hash: None,
        };
        if mode == ImageMode::Standby {
            restored.validate_standby_targets(cut.at_ns)?;
        } else {
            restored.validate_targets(cut.at_ns)?;
        }
        restored.validate_allocations()?;
        Ok(restored)
    }
    /// Capture after fill journal publication, before market acknowledgment. The
    /// caller must bind portfolio and candidate images to the same cut separately.
    pub fn checkpoint(
        &self,
        manifest: &Pinned,
        cut: &Cut,
        last_fills: &BTreeMap<String, Fill>,
        execution_limits: simulation_runtime::checkpoint::Limits,
        maximum_bytes: usize,
    ) -> Result<Bundle> {
        if maximum_bytes == 0
            || maximum_bytes > 64 * 1024 * 1024
            || self.run.manifest_hash() != manifest.hash()
        {
            return Err(Error::Invalid(
                "controller recovery manifest or budget".into(),
            ));
        }
        let run = self.decision_view()?;
        let boundary = run
            .pending()?
            .ok_or_else(|| Error::Unready("controller recovery boundary missing".into()))?;
        if boundary.id != cut.boundary_hash
            || boundary.evaluated_at_ns != cut.at_ns
            || run.status().acknowledged_boundaries.checked_add(1) != Some(cut.boundary_sequence)
        {
            return Err(Error::Conflict("controller recovery cut differs".into()));
        }
        let context = content_hash(&("arte.playback-controller-cut.v1", manifest.hash(), cut))?;
        self.validate_targets(cut.at_ns)?;
        self.validate_allocations()?;
        let execution = self
            .execution
            .checkpoint(manifest, cut, last_fills, execution_limits)?;
        self.image(
            manifest,
            cut,
            ImageMode::Selected,
            &context,
            execution,
            maximum_bytes,
        )
    }

    /// Preserve a preloaded later ticker without treating its market head as
    /// globally selected or its account decisions as already evaluated.
    pub fn checkpoint_standby(
        &self,
        manifest: &Pinned,
        global_cut: &Cut,
        last_fills: &BTreeMap<String, Fill>,
        execution_limits: simulation_runtime::checkpoint::Limits,
        maximum_bytes: usize,
    ) -> Result<Bundle> {
        if maximum_bytes == 0
            || maximum_bytes > 64 * 1024 * 1024
            || self.run.manifest_hash() != manifest.hash()
            || self.dispatched_boundary.is_some()
            || !self.actions.checkpoint()?.is_empty()
        {
            return Err(Error::Conflict(
                "standby controller is not at an idle cut".into(),
            ));
        }
        let boundary = self
            .run
            .pending()?
            .ok_or_else(|| Error::Unready("standby controller market head missing".into()))?;
        if boundary.evaluated_at_ns < global_cut.at_ns
            || self.run.status().acknowledged_boundaries.checked_add(1) != Some(boundary.sequence)
        {
            return Err(Error::Conflict("standby controller head differs".into()));
        }
        self.validate_standby_targets(global_cut.at_ns)?;
        self.validate_allocations()?;
        let scope = self.market_scope();
        let context = content_hash(&(
            "arte.playback-controller-standby.v1",
            manifest.hash(),
            global_cut,
            scope.provider,
            scope.instrument,
            scope.session,
            boundary.id,
            boundary.sequence,
        ))?;
        let execution = self.execution.checkpoint_standby(
            manifest,
            global_cut,
            &self.run,
            last_fills,
            execution_limits,
        )?;
        self.image(
            manifest,
            global_cut,
            ImageMode::Standby,
            &context,
            execution,
            maximum_bytes,
        )
    }

    fn image(
        &self,
        manifest: &Pinned,
        cut: &Cut,
        mode: ImageMode,
        context: &str,
        execution: simulation_runtime::checkpoint::Bundle,
        maximum_bytes: usize,
    ) -> Result<Bundle> {
        let head = self
            .run
            .pending()?
            .ok_or_else(|| Error::Unready("controller image market head missing".into()))?;
        let execution_bytes = execution
            .objects
            .values()
            .try_fold(execution.root.payload.len(), |n, o| {
                n.checked_add(o.payload.len())
            })
            .ok_or_else(|| Error::Capacity("controller recovery size overflow".into()))?;
        let remaining = maximum_bytes
            .checked_sub(execution_bytes)
            .ok_or_else(|| Error::Capacity("controller recovery execution budget".into()))?;
        let playback = self.run.checkpoint(context, remaining)?;
        let scheduler = &playback.playback.scheduler;
        let mut used = execution_bytes;
        for object in [
            &playback.root,
            &playback.playback.root,
            &scheduler.root,
            &scheduler.market,
            &scheduler.trades,
            &scheduler.quotes,
            &scheduler.book,
        ]
        .into_iter()
        .chain(playback.barrier.iter())
        {
            used = used
                .checked_add(object.payload.len())
                .ok_or_else(|| Error::Capacity("controller recovery size overflow".into()))?;
        }
        let mut writer = Writer {
            bytes: Vec::new(),
            maximum: maximum_bytes
                .checked_sub(used)
                .ok_or_else(|| Error::Capacity("controller recovery component budget".into()))?,
        };
        serde_json::to_writer(
            &mut writer,
            &Root {
                version: 6,
                mode,
                manifest_hash: manifest.hash().into(),
                cut: cut.clone(),
                local_head: head.id.into(),
                local_sequence: head.sequence,
                playback: playback.root.id.clone(),
                execution: execution.root.id.clone(),
                maximum_quote_age_ns: self.maximum_quote_age_ns,
                actions: self.actions.checkpoint()?,
                targets: self.targets.clone(),
            },
        )
        .map_err(|e| Error::Serialization(e.to_string()))?;
        Ok(Bundle {
            root: Object::new(writer.bytes),
            playback,
            execution,
        })
    }
}
