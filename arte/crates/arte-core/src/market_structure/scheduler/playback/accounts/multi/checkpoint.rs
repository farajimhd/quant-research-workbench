//! Market/account-barrier snapshot for all historical shards at one global head.
//! Not a whole-run publication: execution, strategy and portfolio are separate.
use super::*;
use crate::{
    content_hash,
    market_structure::scheduler::{
        checkpoint::Request,
        playback::{accounts::checkpoint as account_checkpoint, Prepared},
    },
    quote_state::eligibility,
    seed_storage::Object,
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Root {
    version: u32,
    manifest: String,
    catalog: String,
    selected: (usize, String),
    shards: Vec<(u16, u64, u32, String)>,
}

pub struct Bundle {
    pub root: Object,
    pub shards: Vec<account_checkpoint::Bundle>,
}

/// All restore evidence is independently supplied. The checkpoint does not
/// authorize source data, policy, or a journal row by its own claim.
pub struct RestoreShard<'a> {
    pub prepared: Prepared,
    pub seed_hash: &'a str,
    pub configuration_hash: &'a str,
    pub quote_policy: Arc<eligibility::Pinned>,
    pub maximum_pending: usize,
    pub frames_per_poll: usize,
    pub maximum_consumers: usize,
    pub receipts: Vec<&'a Committed>,
}

fn size(bundle: &account_checkpoint::Bundle) -> Result<usize> {
    let scheduler = &bundle.playback.scheduler;
    [
        &bundle.root,
        &bundle.playback.root,
        &scheduler.root,
        &scheduler.market,
        &scheduler.trades,
        &scheduler.quotes,
        &scheduler.book,
    ]
    .into_iter()
    .chain(bundle.barrier.iter())
    .try_fold(0usize, |used, object| {
        used.checked_add(object.payload.len())
    })
    .ok_or_else(|| Error::Capacity("multi-market checkpoint size overflow".into()))
}

fn budget(maximum_bytes: usize, shards: usize) -> Result<()> {
    if maximum_bytes == 0 || maximum_bytes > 64 * 1024 * 1024 || shards == 0 || shards > 4096 {
        return Err(Error::Capacity("multi-market checkpoint budget".into()));
    }
    Ok(())
}

impl MultiRun {
    /// Captures every market cursor, including unselected future heads. No
    /// ticker is advanced during capture and no account barrier is released.
    pub fn checkpoint(
        &self,
        manifest: &Pinned,
        catalog: &Catalog,
        maximum_bytes: usize,
    ) -> Result<Bundle> {
        budget(maximum_bytes, self.runs.len())?;
        let catalog_hash = catalog.hash()?;
        if catalog_hash != manifest.manifest().source_manifest_hash
            || self.runs.len() != catalog.shards.len()
        {
            return Err(Error::Conflict("multi-market checkpoint source pin".into()));
        }
        let (selected_index, selected_run) = self
            .selected()?
            .ok_or_else(|| Error::Unready("multi-market checkpoint head missing".into()))?;
        let selected_id = selected_run.pending()?.unwrap().id.to_owned();
        let selected = (selected_index, selected_id);
        let context = content_hash(&(
            "arte.multi-market-cut.v1",
            manifest.hash(),
            &catalog_hash,
            &selected,
        ))?;
        let mut used = 0usize;
        let mut shards = Vec::with_capacity(self.runs.len());
        let mut roots = Vec::with_capacity(self.runs.len());
        for (run, source) in self.runs.iter().zip(&catalog.shards) {
            let scope = run.market_scope();
            if run.manifest_hash() != manifest.hash()
                || run.prepared_hash() != source.prepared_hash
                || (scope.provider, scope.instrument, scope.session)
                    != (source.provider, source.instrument, source.session)
            {
                return Err(Error::Conflict(
                    "multi-market checkpoint shard differs".into(),
                ));
            }
            let left = maximum_bytes
                .checked_sub(used)
                .ok_or_else(|| Error::Capacity("multi-market checkpoint byte budget".into()))?;
            let image = run.checkpoint(&context, left)?;
            used = used
                .checked_add(size(&image)?)
                .ok_or_else(|| Error::Capacity("multi-market checkpoint size overflow".into()))?;
            roots.push((
                scope.provider,
                scope.instrument,
                scope.session,
                image.root.id.clone(),
            ));
            shards.push(image);
        }
        let payload = serde_json::to_vec(&Root {
            version: 1,
            manifest: manifest.hash().into(),
            catalog: catalog_hash,
            selected,
            shards: roots,
        })
        .map_err(|e| Error::Serialization(e.to_string()))?;
        if payload.len() > maximum_bytes.saturating_sub(used) {
            return Err(Error::Capacity(
                "multi-market checkpoint root budget".into(),
            ));
        }
        Ok(Bundle {
            root: Object::new(payload),
            shards,
        })
    }

    /// The expected root must come from an independently accepted publication.
    /// Restored runs remain paused at exactly the recorded selected boundary.
    pub fn restore_checkpoint(
        bundle: &Bundle,
        expected_root: &str,
        manifest: &Pinned,
        catalog: &Catalog,
        inputs: Vec<RestoreShard<'_>>,
        maximum_bytes: usize,
    ) -> Result<Self> {
        budget(maximum_bytes, bundle.shards.len())?;
        if bundle.root.id != expected_root
            || bundle.shards.len() != catalog.shards.len()
            || bundle.shards.len() != inputs.len()
        {
            return Err(Error::Conflict(
                "multi-market recovery root or population".into(),
            ));
        }
        let total = bundle
            .shards
            .iter()
            .try_fold(bundle.root.payload.len(), |used, image| {
                used.checked_add(size(image)?)
                    .ok_or_else(|| Error::Capacity("multi-market recovery size overflow".into()))
            })?;
        if total > maximum_bytes {
            return Err(Error::Capacity("multi-market recovery byte budget".into()));
        }
        bundle.root.verify()?;
        let root: Root = serde_json::from_slice(&bundle.root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        let catalog_hash = catalog.hash()?;
        if root.version != 1
            || root.manifest != manifest.hash()
            || root.catalog != catalog_hash
            || catalog_hash != manifest.manifest().source_manifest_hash
            || root.shards.len() != bundle.shards.len()
            || root.selected.0 >= bundle.shards.len()
            || serde_json::to_vec(&root).map_err(|e| Error::Serialization(e.to_string()))?
                != bundle.root.payload
        {
            return Err(Error::Conflict("multi-market recovery pins differ".into()));
        }
        let context = content_hash(&(
            "arte.multi-market-cut.v1",
            manifest.hash(),
            &catalog_hash,
            &root.selected,
        ))?;
        let mut runs = Vec::with_capacity(inputs.len());
        for ((image, input), (source, expected)) in bundle
            .shards
            .iter()
            .zip(inputs)
            .zip(catalog.shards.iter().zip(&root.shards))
        {
            if expected.0 != source.provider
                || expected.1 != source.instrument
                || expected.2 != source.session
                || expected.3 != image.root.id
            {
                return Err(Error::Conflict("multi-market recovery shard pin".into()));
            }
            let request = Request {
                context_hash: &context,
                run_id: &manifest.manifest().run_id,
                seed_hash: input.seed_hash,
                configuration_hash: input.configuration_hash,
                quote_policy: input.quote_policy,
                maximum_pending: input.maximum_pending,
                maximum_bytes,
            };
            runs.push(Run::restore_checkpoint(
                image,
                &expected.3,
                manifest,
                catalog,
                input.prepared,
                request,
                input.frames_per_poll,
                input.maximum_consumers,
                &input.receipts,
            )?);
        }
        let mut restored = Self::new(manifest, catalog, runs)?;
        restored.selected = Some(root.selected);
        restored.selected()?;
        let mut earliest = None;
        for (index, run) in restored.runs.iter().enumerate() {
            let Some(boundary) = run.pending()? else {
                if run.status().mode != crate::market_structure::scheduler::playback::Mode::Complete
                {
                    return Err(Error::Conflict(
                        "multi-market recovery shard has no head".into(),
                    ));
                }
                continue;
            };
            let scope = run.market_scope();
            let order = (
                boundary.evaluated_at_ns,
                boundary.input(String::new()).event_time_ns,
                scope.provider,
                scope.instrument,
                scope.session,
                boundary.sequence,
            );
            if earliest.as_ref().is_none_or(|(prior, _, _)| order < *prior) {
                earliest = Some((order, index, boundary.id.to_owned()));
            }
        }
        if earliest.is_none_or(|(_, index, id)| (index, id) != restored.selected.clone().unwrap()) {
            return Err(Error::Conflict(
                "multi-market recovery selected head is not earliest".into(),
            ));
        }
        if restored
            .checkpoint(manifest, catalog, maximum_bytes)?
            .root
            .id
            != expected_root
        {
            return Err(Error::Conflict(
                "multi-market recovery image differs".into(),
            ));
        }
        Ok(restored)
    }
}
