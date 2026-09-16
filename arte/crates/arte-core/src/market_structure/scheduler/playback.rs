//! In-process historical/debug control over the shared causal scheduler.
//! No transport, live credentials, wall clock, or alternative strategy implementation.
use super::{Boundary, Runtime, Scheduler};
use crate::{content_hash, event_order::Scope, events::Observation, Error, Result};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::sync::Arc;
pub mod accounts;

#[derive(Debug, Clone, Serialize)]
pub struct Input {
    pub observation: Observation,
    pub eligible: bool,
}
/// A source producer justifies the watermark and modeled evaluation clock.
/// Playback never substitutes these clocks into raw event timestamps.
#[derive(Debug, Clone, Serialize)]
pub struct Frame {
    pub watermark_ns: u64,
    pub evaluated_at_ns: u64,
    pub inputs: Vec<Input>,
}
pub struct Limits {
    pub maximum_frames: usize,
    pub maximum_events: usize,
    pub maximum_serialized_bytes: usize,
}
/// Immutable prepared input can be shared by independent parameter runs.
#[derive(Clone)]
pub struct Prepared {
    scope: Scope,
    frames: Arc<[Frame]>,
    hash: String,
    events: usize,
}
impl Prepared {
    pub fn new(
        scope: Scope,
        clock_model: &str,
        frames: Vec<Frame>,
        limits: Limits,
    ) -> Result<Self> {
        if limits.maximum_frames == 0
            || limits.maximum_frames > 10_000_000
            || limits.maximum_events == 0
            || limits.maximum_events > 10_000_000
            || limits.maximum_serialized_bytes == 0
            || frames.is_empty()
            || frames.len() > limits.maximum_frames
        {
            return Err(Error::Capacity("prepared playback input budget".into()));
        }
        if clock_model.is_empty() || clock_model.len() > 128 {
            return Err(Error::Invalid(
                "explicit replay clock model required".into(),
            ));
        }
        crate::event_order::Buffer::new(scope, 1, 0)?;
        let mut events = 0usize;
        let mut bytes = 0usize;
        let mut prior = (0, 0);
        let mut digest = Sha256::new();
        digest.update(
            content_hash(&(
                "arte.prepared-playback.v1",
                (scope.provider, scope.instrument, scope.session),
                clock_model,
            ))?
            .as_bytes(),
        );
        for frame in &frames {
            if frame.inputs.len() > 256 {
                return Err(Error::Capacity("playback frame exceeds 256 events".into()));
            }
            if frame.watermark_ns < prior.0
                || frame.evaluated_at_ns < prior.1
                || frame.watermark_ns > frame.evaluated_at_ns
            {
                return Err(Error::Invalid(
                    "playback clock rewind or future watermark".into(),
                ));
            }
            for input in &frame.inputs {
                let event = &input.observation;
                event.validate()?;
                if event.key.provider != scope.provider
                    || event.key.instrument != scope.instrument
                    || event.key.session != scope.session
                    || event.key.kind != crate::events::EventKind::Trade
                    || event.available_at_ns > frame.evaluated_at_ns
                    || event.sip.ns < prior.0
                {
                    return Err(Error::Invalid(
                        "playback trade scope, availability or late input".into(),
                    ));
                }
            }
            events = events
                .checked_add(frame.inputs.len())
                .ok_or_else(|| Error::Capacity("playback event overflow".into()))?;
            let serialized =
                serde_json::to_vec(frame).map_err(|e| Error::Serialization(e.to_string()))?;
            bytes = bytes
                .checked_add(serialized.len())
                .ok_or_else(|| Error::Capacity("playback byte overflow".into()))?;
            if events > limits.maximum_events || bytes > limits.maximum_serialized_bytes {
                return Err(Error::Capacity("prepared playback input budget".into()));
            }
            digest.update((serialized.len() as u64).to_le_bytes());
            digest.update(&serialized);
            prior = (frame.watermark_ns, frame.evaluated_at_ns);
        }
        let hash = format!("{:x}", digest.finalize());
        Ok(Self {
            scope,
            frames: frames.into(),
            hash,
            events,
        })
    }
    pub fn hash(&self) -> &str {
        &self.hash
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum Mode {
    Paused,
    Playing,
    Stepping,
    Complete,
    Failed,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Poll {
    Paused,
    Boundary,
    Yield,
    Complete,
}
#[derive(Debug, Serialize)]
pub struct Status {
    pub mode: Mode,
    pub prepared_hash: String,
    pub total_frames: usize,
    pub completed_frames: usize,
    pub total_events: usize,
    pub admitted_events: usize,
    pub coalesced_events: usize,
    pub queued_events: usize,
    pub acknowledged_boundaries: u64,
    pub pending_boundary: bool,
    pub failure: Option<String>,
}
pub struct Playback {
    scheduler: Scheduler,
    prepared: Prepared,
    frame: usize,
    admitted_in_frame: usize,
    admitted_events: usize,
    coalesced_events: usize,
    acknowledged: u64,
    mode: Mode,
    failure: Option<String>,
    frames_per_poll: usize,
}
impl Playback {
    pub fn new(scheduler: Scheduler, prepared: Prepared, frames_per_poll: usize) -> Result<Self> {
        if frames_per_poll == 0 || frames_per_poll > 4096 {
            return Err(Error::Capacity("playback poll work budget".into()));
        }
        if scheduler.scope() != prepared.scope
            || scheduler.sequence != 0
            || scheduler.pending()?.is_some()
            || scheduler.pending_events() != 0
            || prepared.frames[0].watermark_ns < scheduler.watermark_ns()
        {
            return Err(Error::Conflict(
                "playback requires matching empty scheduler".into(),
            ));
        }
        Ok(Self {
            scheduler,
            prepared,
            frame: 0,
            admitted_in_frame: 0,
            admitted_events: 0,
            coalesced_events: 0,
            acknowledged: 0,
            mode: Mode::Paused,
            failure: None,
            frames_per_poll,
        })
    }
    fn controllable(&self) -> Result<()> {
        if matches!(self.mode, Mode::Complete | Mode::Failed) {
            return Err(Error::Unready("playback is terminal".into()));
        }
        Ok(())
    }
    pub fn pause(&mut self) -> Result<()> {
        self.controllable()?;
        self.mode = Mode::Paused;
        Ok(())
    }
    pub fn resume(&mut self) -> Result<()> {
        self.controllable()?;
        self.mode = Mode::Playing;
        Ok(())
    }
    pub fn step(&mut self) -> Result<()> {
        self.controllable()?;
        if self.mode != Mode::Paused || self.scheduler.pending()?.is_some() {
            return Err(Error::Unready(
                "step requires paused playback without pending consumers".into(),
            ));
        }
        self.mode = Mode::Stepping;
        Ok(())
    }
    pub fn market(&self) -> Result<&Runtime> {
        self.scheduler.state()
    }
    pub fn pending(&self) -> Result<Option<Boundary<'_>>> {
        self.scheduler.pending()
    }
    pub fn status(&self) -> Status {
        Status {
            mode: self.mode,
            prepared_hash: self.prepared.hash.clone(),
            total_frames: self.prepared.frames.len(),
            completed_frames: self.frame,
            total_events: self.prepared.events,
            admitted_events: self.admitted_events,
            coalesced_events: self.coalesced_events,
            queued_events: self.scheduler.pending_events(),
            acknowledged_boundaries: self.acknowledged,
            pending_boundary: self.scheduler.pending.is_some(),
            failure: self.failure.clone(),
        }
    }
    /// Caller must first commit all required account journals and execution work.
    /// This is a local cursor acknowledgment, not a durability receipt.
    pub fn acknowledge(&mut self, id: &str) -> Result<()> {
        self.controllable()?;
        let next = self
            .acknowledged
            .checked_add(1)
            .ok_or_else(|| Error::Capacity("playback acknowledgment overflow".into()))?;
        self.scheduler.acknowledge(id)?;
        self.acknowledged = next;
        Ok(())
    }
    pub fn poll(&mut self) -> Result<Poll> {
        if self.mode == Mode::Complete {
            return Ok(Poll::Complete);
        }
        self.controllable()?;
        let result = self.advance();
        if let Err(error) = &result {
            self.mode = Mode::Failed;
            self.failure = Some(error.to_string());
        }
        result
    }
    fn advance(&mut self) -> Result<Poll> {
        if self.scheduler.pending()?.is_some() {
            return Ok(Poll::Boundary);
        }
        if self.mode == Mode::Paused {
            return Ok(Poll::Paused);
        }
        for _ in 0..self.frames_per_poll {
            let Some(frame) = self.prepared.frames.get(self.frame) else {
                if self.scheduler.pending_events() != 0 {
                    return Err(Error::Unready(
                        "replay ends before the final watermark releases all events".into(),
                    ));
                }
                self.mode = Mode::Complete;
                return Ok(Poll::Complete);
            };
            while let Some(input) = frame.inputs.get(self.admitted_in_frame) {
                if self.scheduler.enqueue(&input.observation, input.eligible)? {
                    self.admitted_events += 1;
                } else {
                    self.coalesced_events += 1;
                }
                self.admitted_in_frame += 1;
            }
            if self
                .scheduler
                .prepare_next(frame.watermark_ns, frame.evaluated_at_ns)?
            {
                if self.mode == Mode::Stepping {
                    self.mode = Mode::Paused;
                }
                return Ok(Poll::Boundary);
            }
            self.frame += 1;
            self.admitted_in_frame = 0;
        }
        Ok(Poll::Yield)
    }
}
