//! Pausable causal consumption of one instrument's ordered market stream.
//! A pending boundary owns the pause across asynchronous journal work. This is
//! neither a durable recovery record nor permission to submit broker orders.
use super::{ObservationUpdate, Ordered, Runtime};
use crate::{
    events::{EventKey, Observation},
    market::Completed,
    Error, Result,
};

pub struct Scheduler {
    market: Ordered,
    run_id: String,
    sequence: u64,
    pending: Option<Pending>,
}

struct Pending {
    id: String,
    evaluated_at_ns: u64,
    kind: PendingKind,
}
enum PendingKind {
    Completed(usize),
    Trade { key: EventKey, eligible: bool },
}
/// Borrowed views cannot outlive the scheduler's next mutable operation. The
/// original trade receipt and source clocks remain in the observation unchanged.
pub struct Boundary<'a> {
    pub id: &'a str,
    /// Shared bar/trade boundary sequence, not the provider's event sequence.
    pub sequence: u64,
    pub evaluated_at_ns: u64,
    pub kind: Kind<'a>,
}
pub enum Kind<'a> {
    Completed(&'a Completed),
    Trade {
        observation: &'a Observation,
        eligible: bool,
    },
}
impl Boundary<'_> {
    /// The completed-bar computation becomes available at evaluation. This does
    /// not fabricate a historical market receipt. Trade availability stays raw.
    pub fn input(&self, feature_hash: String) -> crate::strategy_dispatch::InputBoundary {
        let (event_time_ns, available_at_ns) = match &self.kind {
            Kind::Completed(bar) => (bar.bar.end_ns, self.evaluated_at_ns),
            Kind::Trade { observation, .. } => (observation.sip.ns, observation.available_at_ns),
        };
        crate::strategy_dispatch::InputBoundary {
            event_id: self.id.into(),
            event_time_ns,
            available_at_ns,
            evaluated_at_ns: self.evaluated_at_ns,
            source_sequence: self.sequence,
            feature_hash,
        }
    }
}
impl Scheduler {
    pub fn new(market: Ordered, run_id: String) -> Result<Self> {
        market.available()?;
        if run_id.is_empty() || run_id.len() > 128 {
            return Err(Error::Invalid("causal scheduler run identity".into()));
        }
        Ok(Self {
            market,
            run_id,
            sequence: 0,
            pending: None,
        })
    }
    pub fn enqueue(&mut self, event: &Observation, eligible: bool) -> Result<bool> {
        self.market.enqueue(event, eligible)
    }
    pub fn state(&self) -> Result<&Runtime> {
        self.market.available()?;
        self.market.runtime.available()?;
        Ok(&self.market.runtime)
    }
    pub fn pending_events(&self) -> usize {
        self.market.pending()
    }
    pub fn scope(&self) -> crate::event_order::Scope {
        self.market.scope()
    }
    pub fn watermark_ns(&self) -> u64 {
        self.market.watermark_ns()
    }
    /// Repeated reads return the same boundary while a consumer awaits durable
    /// acknowledgment. Merely dropping this borrowed view does not consume it.
    pub fn pending(&self) -> Result<Option<Boundary<'_>>> {
        self.state()?;
        let Some(pending) = &self.pending else {
            return Ok(None);
        };
        let kind = match &pending.kind {
            PendingKind::Completed(index) => Kind::Completed(
                self.market
                    .runtime
                    .market
                    .completed()
                    .get(*index)
                    .ok_or_else(|| Error::Conflict("pending completed bar missing".into()))?,
            ),
            PendingKind::Trade { key, eligible } => {
                let observation = self
                    .market
                    .buffer
                    .first_releasable()
                    .filter(|event| &event.key == key)
                    .ok_or_else(|| Error::Conflict("pending trade missing".into()))?;
                Kind::Trade {
                    observation,
                    eligible: *eligible,
                }
            }
        };
        Ok(Some(Boundary {
            id: &pending.id,
            sequence: self.sequence,
            evaluated_at_ns: pending.evaluated_at_ns,
            kind,
        }))
    }
    /// Prepare exactly one boundary. For a trade in a later bar, first close and
    /// expose the earlier bar WITHOUT applying that trade. Empty intervals do not
    /// manufacture bars. Caller supplies the externally justified watermark and
    /// the actual evaluation clock (or the explicitly modeled replay clock).
    pub fn prepare_next(&mut self, watermark_ns: u64, evaluated_at_ns: u64) -> Result<bool> {
        self.state()?;
        if self.pending.is_some() {
            return Err(Error::Unready(
                "acknowledge pending causal boundary first".into(),
            ));
        }
        self.market.runtime.clock(watermark_ns, evaluated_at_ns)?;
        if evaluated_at_ns < self.market.newest_receipt_ns {
            return Err(Error::Invalid(
                "causal evaluation precedes received input".into(),
            ));
        }
        let next_sequence = self
            .sequence
            .checked_add(1)
            .ok_or_else(|| Error::Capacity("causal boundary sequence exhausted".into()))?;
        let result = (|| {
            self.market.buffer.begin_release(watermark_ns)?;
            let before = self.market.runtime.market.completed().len();
            let kind = if let Some(event) = self.market.buffer.first_releasable() {
                let eligible = *self
                    .market
                    .eligibility
                    .get(&event.key)
                    .ok_or_else(|| Error::Unready("causal trade eligibility missing".into()))?;
                self.market.runtime.advance(event.sip.ns, evaluated_at_ns)?;
                if self.market.runtime.market.completed().len() > before {
                    PendingKind::Completed(before)
                } else {
                    match self.market.runtime.observe_ordered_trade(
                        event,
                        eligible,
                        evaluated_at_ns,
                    )? {
                        ObservationUpdate::Applied(_) => PendingKind::Trade {
                            key: event.key.clone(),
                            eligible,
                        },
                        ObservationUpdate::Duplicate => {
                            return Err(Error::Conflict(
                                "unacknowledged event was already applied".into(),
                            ))
                        }
                    }
                }
            } else {
                self.market.runtime.advance(watermark_ns, evaluated_at_ns)?;
                if self.market.runtime.market.completed().len() == before {
                    return Ok(false);
                }
                PendingKind::Completed(before)
            };
            let scope = self.market.scope();
            let id = crate::content_hash(&(
                "causal-market-boundary-v1",
                &self.run_id,
                (scope.provider, scope.instrument, scope.session),
                self.market.runtime.configuration_hash(),
                next_sequence,
                evaluated_at_ns,
                match &kind {
                    PendingKind::Completed(index) => {
                        crate::content_hash(&self.market.runtime.market.completed()[*index])?
                    }
                    PendingKind::Trade { key, eligible } => crate::content_hash(&(key, eligible))?,
                },
            ))?;
            self.pending = Some(Pending {
                id,
                evaluated_at_ns,
                kind,
            });
            self.sequence = next_sequence;
            Ok(true)
        })();
        if result.is_err() {
            self.market.failed = true;
        }
        result
    }
    /// Consumer calls this only after its required decision journal work succeeds.
    /// This local acknowledgment does not itself verify a journal or broker state.
    /// Wrong identities leave the pending boundary intact for correct retry.
    pub fn acknowledge(&mut self, id: &str) -> Result<()> {
        self.state()?;
        let pending = self
            .pending
            .as_ref()
            .ok_or_else(|| Error::Unready("no pending causal boundary".into()))?;
        if pending.id != id {
            return Err(Error::Conflict(
                "causal boundary acknowledgment identity".into(),
            ));
        }
        if let PendingKind::Trade { key, .. } = &pending.kind {
            self.market.buffer.acknowledge_first(key)?;
            self.market.eligibility.remove(key);
        }
        self.pending = None;
        Ok(())
    }
}

#[cfg(test)]
#[path = "scheduler_tests.rs"]
mod tests;
