//! Manifest-bound playback plus its exact independently verified journal barrier.
use super::*;
use crate::seed_storage::Object;
use serde::{Deserialize, Serialize};

pub struct Bundle {
    pub root: Object,
    pub playback: super::super::checkpoint::Bundle,
    pub barrier: Option<Object>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Root {
    version: u32,
    manifest_hash: String,
    playback_hash: String,
    barrier_hash: Option<String>,
    maximum_consumers: usize,
}
fn budget(maximum: usize, barrier_bytes: usize) -> Result<usize> {
    if maximum > 64 * 1024 * 1024 || barrier_bytes > 1024 * 1024 {
        return Err(Error::Capacity("account playback recovery budget".into()));
    }
    maximum
        .checked_sub(4096 + barrier_bytes)
        .ok_or_else(|| Error::Capacity("account playback recovery budget".into()))
}
impl Run {
    pub fn checkpoint(&self, context: &str, maximum_bytes: usize) -> Result<Bundle> {
        budget(maximum_bytes, 0)?;
        if self.playback.pending()?.is_some() != self.barrier.is_some() {
            return Err(Error::Unready("playback barrier not synchronized".into()));
        }
        let barrier = self
            .barrier
            .as_ref()
            .map(|b| b.checkpoint(context, maximum_bytes.min(1024 * 1024)))
            .transpose()?;
        let playback = self.playback.checkpoint(
            context,
            budget(
                maximum_bytes,
                barrier.as_ref().map_or(0, |b| b.payload.len()),
            )?,
        )?;
        let root = Root {
            version: 1,
            manifest_hash: self.manifest_hash.clone(),
            playback_hash: playback.root.id.clone(),
            barrier_hash: barrier.as_ref().map(|b| b.id.clone()),
            maximum_consumers: self.maximum_consumers,
        };
        let bytes = serde_json::to_vec(&root).map_err(|e| Error::Serialization(e.to_string()))?;
        if bytes.len() > 4096 {
            return Err(Error::Capacity("account playback root budget".into()));
        }
        Ok(Bundle {
            root: Object::new(bytes),
            playback,
            barrier,
        })
    }

    #[allow(clippy::too_many_arguments)]
    pub fn restore_checkpoint(
        bundle: &Bundle,
        expected_root: &str,
        manifest: &Pinned,
        sources: &super::super::sources::Catalog,
        prepared: Prepared,
        mut request: super::super::super::checkpoint::Request<'_>,
        frames_per_poll: usize,
        maximum_consumers: usize,
        receipts: &[&Committed],
    ) -> Result<Self> {
        sources.require(manifest, &prepared)?;
        if manifest.manifest().mode != Mode::Backtest
            || request.run_id != manifest.manifest().run_id
            || bundle.root.id != expected_root
            || bundle.root.payload.len() > 4096
        {
            return Err(Error::Conflict("account playback recovery identity".into()));
        }
        request.maximum_bytes = budget(
            request.maximum_bytes,
            bundle.barrier.as_ref().map_or(0, |b| b.payload.len()),
        )?;
        bundle.root.verify()?;
        let root: Root = serde_json::from_slice(&bundle.root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if root.version != 1
            || root.manifest_hash != manifest.hash()
            || root.playback_hash != bundle.playback.root.id
            || root.barrier_hash.as_deref() != bundle.barrier.as_ref().map(|b| b.id.as_str())
            || root.maximum_consumers != maximum_consumers
            || serde_json::to_vec(&root).map_err(|e| Error::Serialization(e.to_string()))?
                != bundle.root.payload
        {
            return Err(Error::Conflict(
                "account playback recovery pins differ".into(),
            ));
        }
        let context = request.context_hash;
        let playback = Playback::restore_checkpoint(
            &bundle.playback,
            &root.playback_hash,
            prepared,
            request,
            frames_per_poll,
        )?;
        let scopes = Self::consumer_scopes(
            manifest,
            playback.scheduler.scope().instrument,
            maximum_consumers,
        )?;
        let barrier = match (playback.pending()?, &bundle.barrier) {
            (Some(boundary), Some(image)) => Some(Barrier::restore_checkpoint(
                image,
                &image.id,
                context,
                boundary.input(String::new()),
                &scopes,
                maximum_consumers,
                1024 * 1024,
                receipts,
            )?),
            (None, None) if receipts.is_empty() => None,
            _ => {
                return Err(Error::Conflict(
                    "account playback recovery barrier differs".into(),
                ))
            }
        };
        Ok(Self {
            playback,
            manifest_hash: root.manifest_hash,
            scopes,
            barrier,
            maximum_consumers,
        })
    }
}
