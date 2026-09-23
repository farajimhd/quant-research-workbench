//! Small restart image for the bar cursor. Verified columnar products are
//! supplied again on restore; their rows never enter this checkpoint.
use super::*;
use crate::seed_storage::Object;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

const VERSION: u32 = 1;
const MAXIMUM_BYTES: usize = 16 * 1024 * 1024;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Snapshot {
    version: u32,
    provider: u16,
    request_hashes: Vec<String>,
    coverage_hashes: Vec<String>,
    include_empty: bool,
    fixed_interval_ns: Option<u64>,
    last_end_ns: Option<u64>,
    cursors: Vec<Cursor>,
    ready: Vec<Head>,
}
fn budget(maximum_bytes: usize) -> Result<()> {
    if maximum_bytes == 0 || maximum_bytes > MAXIMUM_BYTES {
        return Err(Error::Capacity("bar tape checkpoint budget".into()));
    }
    Ok(())
}
impl Tape {
    pub fn checkpoint(&self, maximum_bytes: usize) -> Result<Object> {
        budget(maximum_bytes)?;
        let mut ready: Vec<_> = self.ready.iter().map(|head| head.0).collect();
        ready.sort_unstable();
        let snapshot = Snapshot {
            version: VERSION,
            provider: self.provider,
            request_hashes: self.request_hashes.clone(),
            coverage_hashes: self
                .products
                .iter()
                .map(|product| product.coverage_hash().to_owned())
                .collect(),
            include_empty: self.include_empty,
            fixed_interval_ns: self.fixed_interval_ns,
            last_end_ns: self.last_end_ns,
            cursors: self.tracks.iter().map(|track| track.cursor).collect(),
            ready,
        };
        let payload =
            serde_json::to_vec(&snapshot).map_err(|e| Error::Serialization(e.to_string()))?;
        if payload.len() > maximum_bytes {
            return Err(Error::Capacity("bar tape checkpoint bytes".into()));
        }
        Ok(Object::new(payload))
    }

    /// `expected_hash` must come from an independent durable publication.
    /// Restore verifies source identity and cursor shape without replaying bars.
    pub fn restore_checkpoint(
        products: Vec<Complete>,
        image: &Object,
        expected_hash: &str,
        maximum_bytes: usize,
    ) -> Result<Self> {
        budget(maximum_bytes)?;
        if image.id != expected_hash || image.payload.len() > maximum_bytes {
            return Err(Error::Conflict(
                "bar tape checkpoint identity or budget".into(),
            ));
        }
        image.verify()?;
        let snapshot: Snapshot = serde_json::from_slice(&image.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if snapshot.version != VERSION
            || serde_json::to_vec(&snapshot).map_err(|e| Error::Serialization(e.to_string()))?
                != image.payload
            || (snapshot.fixed_interval_ns.is_some() && !snapshot.include_empty)
        {
            return Err(Error::Conflict("bar tape checkpoint format".into()));
        }
        if let Some(ns) = snapshot.fixed_interval_ns {
            ExecutionInterval::Fixed(ns).validate()?;
        }
        let mut tape =
            Self::with_mode(products, snapshot.include_empty, snapshot.fixed_interval_ns)?;
        if snapshot.provider != tape.provider
            || snapshot.request_hashes != tape.request_hashes
            || snapshot.coverage_hashes
                != tape
                    .products
                    .iter()
                    .map(|product| product.coverage_hash().to_owned())
                    .collect::<Vec<_>>()
            || snapshot.cursors.len() != tape.tracks.len()
            || snapshot.ready.len() > tape.tracks.len()
        {
            return Err(Error::Conflict("bar tape checkpoint sources differ".into()));
        }
        let mut seen = BTreeSet::new();
        for head in &snapshot.ready {
            let track = tape
                .tracks
                .get(head.track)
                .ok_or_else(|| Error::Conflict("bar tape checkpoint track".into()))?;
            let cursor = snapshot.cursors[head.track];
            if !seen.insert(head.track)
                || head.instrument != track.instrument
                || head.session != track.session
                || cursor.batch < track.first_batch
                || cursor.batch >= track.end_batch
                || cursor.slot == 0
            {
                return Err(Error::Conflict("bar tape checkpoint head".into()));
            }
            let batch = &tape.products[track.product].batches()[cursor.batch];
            if cursor.slot > batch.count as usize {
                return Err(Error::Conflict("bar tape checkpoint slot".into()));
            }
            let slot = cursor.slot - 1;
            let end_ns = batch
                .first_start_ns
                .checked_add(
                    (slot as u64 + 1) * tape.products[track.product].request().timeframe_ns,
                )
                .ok_or_else(|| Error::Capacity("bar tape checkpoint clock".into()))?;
            if batch.instrument != track.instrument
                || head.end_ns != end_ns
                || (!tape.include_empty && !batch.present[slot])
                || tape
                    .fixed_interval_ns
                    .is_some_and(|ns| !end_ns.is_multiple_of(ns))
                || snapshot.last_end_ns.is_some_and(|last| end_ns < last)
            {
                return Err(Error::Conflict("bar tape checkpoint queued bar".into()));
            }
        }
        for (index, &cursor) in snapshot.cursors.iter().enumerate() {
            if !seen.contains(&index)
                && (cursor.batch != tape.tracks[index].end_batch || cursor.slot != 0)
            {
                return Err(Error::Conflict("bar tape checkpoint unqueued track".into()));
            }
            tape.tracks[index].cursor = cursor;
        }
        tape.ready = snapshot.ready.into_iter().map(Reverse).collect();
        tape.last_end_ns = snapshot.last_end_ns;
        if tape.checkpoint(maximum_bytes)?.payload != image.payload {
            return Err(Error::Conflict(
                "bar tape checkpoint reconstruction differs".into(),
            ));
        }
        Ok(tape)
    }
}
