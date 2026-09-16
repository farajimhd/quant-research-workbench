//! Prepared inputs are externally pinned, not duplicated in recovery images.
//! Restore pauses nonterminal playback; it never acknowledges account journals.
use super::*;
use crate::seed_storage::Object;
use serde::{Deserialize, Serialize};

pub struct Bundle {
    pub root: Object,
    pub scheduler: super::super::checkpoint::Bundle,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Cursor {
    version: u32,
    prepared_hash: String,
    scheduler_hash: String,
    frame: usize,
    admitted_in_frame: usize,
    admitted_events: usize,
    coalesced_events: usize,
    acknowledged: u64,
    complete: bool,
    frames_per_poll: usize,
}
impl Playback {
    pub fn checkpoint(&self, context: &str, maximum_bytes: usize) -> Result<Bundle> {
        if self.mode == Mode::Failed || self.failure.is_some() {
            return Err(Error::Unready("failed playback cannot checkpoint".into()));
        }
        // Reserve a fixed bound for the small cursor before encoding components.
        let child_budget = maximum_bytes
            .checked_sub(4096)
            .ok_or_else(|| Error::Capacity("playback recovery budget".into()))?;
        if maximum_bytes > 64 * 1024 * 1024 {
            return Err(Error::Capacity("playback recovery budget".into()));
        }
        let scheduler = self.scheduler.checkpoint(context, child_budget)?;
        let cursor = Cursor {
            version: 1,
            prepared_hash: self.prepared.hash.clone(),
            scheduler_hash: scheduler.root.id.clone(),
            frame: self.frame,
            admitted_in_frame: self.admitted_in_frame,
            admitted_events: self.admitted_events,
            coalesced_events: self.coalesced_events,
            acknowledged: self.acknowledged,
            complete: self.mode == Mode::Complete,
            frames_per_poll: self.frames_per_poll,
        };
        let payload =
            serde_json::to_vec(&cursor).map_err(|e| Error::Serialization(e.to_string()))?;
        if payload.len() > 4096 {
            return Err(Error::Capacity("playback cursor budget".into()));
        }
        Ok(Bundle {
            root: Object::new(payload),
            scheduler,
        })
    }

    pub fn restore_checkpoint(
        bundle: &Bundle,
        expected_root: &str,
        prepared: Prepared,
        mut request: super::super::checkpoint::Request<'_>,
        frames_per_poll: usize,
    ) -> Result<Self> {
        if bundle.root.id != expected_root
            || bundle.root.payload.len() > 4096
            || request.maximum_bytes > 64 * 1024 * 1024
            || frames_per_poll == 0
            || frames_per_poll > 4096
        {
            return Err(Error::Invalid(
                "playback recovery identity or budget".into(),
            ));
        }
        request.maximum_bytes = request
            .maximum_bytes
            .checked_sub(4096)
            .ok_or_else(|| Error::Capacity("playback recovery budget".into()))?;
        bundle.root.verify()?;
        let cursor: Cursor = serde_json::from_slice(&bundle.root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if cursor.version != 1
            || cursor.prepared_hash != prepared.hash
            || cursor.scheduler_hash != bundle.scheduler.root.id
            || cursor.frames_per_poll != frames_per_poll
            || serde_json::to_vec(&cursor).map_err(|e| Error::Serialization(e.to_string()))?
                != bundle.root.payload
        {
            return Err(Error::Conflict("playback recovery pins differ".into()));
        }
        let scheduler =
            Scheduler::restore_checkpoint(&bundle.scheduler, &cursor.scheduler_hash, request)?;
        if scheduler.scope() != prepared.scope || cursor.frame > prepared.frames.len() {
            return Err(Error::Conflict("playback recovery scope or cursor".into()));
        }
        let active = prepared.frames.get(cursor.frame);
        if cursor.admitted_in_frame != 0
            && cursor.admitted_in_frame != active.map_or(0, |f| f.inputs.len())
        {
            return Err(Error::Conflict("playback admission cursor".into()));
        }
        let consumed = prepared.frames[..cursor.frame]
            .iter()
            .try_fold(cursor.admitted_in_frame, |n, f| {
                n.checked_add(f.inputs.len())
            })
            .ok_or_else(|| Error::Capacity("playback cursor overflow".into()))?;
        let pending = scheduler.pending()?.is_some();
        if cursor.admitted_events.checked_add(cursor.coalesced_events) != Some(consumed)
            || cursor.acknowledged.checked_add(u64::from(pending)) != Some(scheduler.sequence)
            || (cursor.complete && (active.is_some() || pending || scheduler.pending_events() != 0))
            || (pending && active.is_none())
        {
            return Err(Error::Conflict(
                "playback recovery counters or completion".into(),
            ));
        }
        if cursor.frame > 0
            && scheduler.watermark_ns() < prepared.frames[cursor.frame - 1].watermark_ns
        {
            return Err(Error::Conflict(
                "playback recovery watermark behind cursor".into(),
            ));
        }
        if active.is_none()
            && scheduler.watermark_ns() != prepared.frames[prepared.frames.len() - 1].watermark_ns
        {
            return Err(Error::Conflict("playback final watermark differs".into()));
        }
        if let Some(frame) = active {
            if scheduler.watermark_ns() > frame.watermark_ns
                || (pending
                    && (cursor.admitted_in_frame != frame.inputs.len()
                        || scheduler.watermark_ns() != frame.watermark_ns
                        || scheduler
                            .pending()?
                            .is_some_and(|p| p.evaluated_at_ns != frame.evaluated_at_ns)))
            {
                return Err(Error::Conflict(
                    "playback recovery active frame differs".into(),
                ));
            }
        }
        Ok(Self {
            scheduler,
            prepared,
            frame: cursor.frame,
            admitted_in_frame: cursor.admitted_in_frame,
            admitted_events: cursor.admitted_events,
            coalesced_events: cursor.coalesced_events,
            acknowledged: cursor.acknowledged,
            mode: if cursor.complete {
                Mode::Complete
            } else {
                Mode::Paused
            },
            failure: None,
            frames_per_poll,
        })
    }
}
