//! Pausable causal consumption of one instrument's ordered market stream.
//! A pending boundary owns the pause across asynchronous journal work. This is
//! neither a durable recovery record nor permission to submit broker orders.
use super::{ObservationUpdate, Ordered, Runtime};
use crate::{
    events::{EventKey, Observation},
    market::Completed,
    Error, Result,
};
pub mod checkpoint;
pub mod playback;
mod quotes;

pub struct Scheduler {
    market: Ordered,
    run_id: String,
    sequence: u64,
    pending: Option<Pending>,
    completed: std::collections::VecDeque<(u64, usize, u64)>,
    quotes: quotes::Quotes,
}

#[derive(Clone, serde::Serialize, serde::Deserialize)]
struct Pending {
    id: String,
    evaluated_at_ns: u64,
    kind: PendingKind,
}
#[derive(Clone, serde::Serialize, serde::Deserialize)]
enum PendingKind {
    Quote {
        key: EventKey,
    },
    Completed {
        interval_ns: u64,
        index: usize,
        available_at_ns: u64,
    },
    Trade {
        key: EventKey,
        eligible: bool,
    },
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
    Quote {
        observation: &'a Observation,
    },
    Completed {
        interval_ns: u64,
        bar: &'a Completed,
        available_at_ns: u64,
    },
    Trade {
        observation: &'a Observation,
        eligible: bool,
    },
}
impl Boundary<'_> {
    /// A computation runs only on its declared clock. Event cadence observes
    /// source events; fixed cadence observes the matching completed bar, never
    /// a later bar or a repeated interpolation of an earlier one.
    pub fn due_for(&self, route: crate::execution_interval::Route) -> bool {
        match (&self.kind, route.interval()) {
            (
                Kind::Trade { .. } | Kind::Quote { .. },
                crate::execution_interval::ExecutionInterval::Events,
            ) => true,
            (
                Kind::Completed {
                    interval_ns, bar, ..
                },
                crate::execution_interval::ExecutionInterval::Fixed(ns),
            ) => *interval_ns == ns && bar.bar.end_ns.is_multiple_of(ns),
            _ => false,
        }
    }
    /// The completed-bar computation retains its first availability. This does
    /// not fabricate a historical market receipt. Trade availability stays raw.
    pub fn input(&self, feature_hash: String) -> crate::strategy_dispatch::InputBoundary {
        let (event_time_ns, available_at_ns) = match &self.kind {
            Kind::Completed {
                bar,
                available_at_ns,
                ..
            } => (bar.bar.end_ns, *available_at_ns),
            Kind::Trade { observation, .. } | Kind::Quote { observation } => {
                (observation.sip.ns, observation.available_at_ns)
            }
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
        let quotes = quotes::Quotes::new(
            market.scope(),
            market.buffer.maximum(),
            market.runtime.maximum_market_events,
            market.watermark_ns(),
        )?;
        Ok(Self {
            market,
            run_id,
            sequence: 0,
            pending: None,
            completed: std::collections::VecDeque::new(),
            quotes,
        })
    }
    pub fn enqueue(&mut self, event: &Observation, eligible: bool) -> Result<bool> {
        if event.key.kind == crate::events::EventKind::Quote {
            self.state()?;
            if eligible {
                return Err(Error::Invalid(
                    "quote cannot carry trade eligibility".into(),
                ));
            }
            let result = self.quotes.enqueue(event);
            if result.is_err() {
                self.market.failed = true;
            }
            return result;
        }
        self.market.enqueue(event, eligible)
    }
    pub fn quotes(&self) -> Result<&crate::quote_state::Book> {
        self.state()?;
        Ok(&self.quotes.book)
    }
    pub fn bind_quote_policy(
        &mut self,
        policy: std::sync::Arc<crate::quote_state::eligibility::Pinned>,
    ) -> Result<()> {
        self.state()?;
        self.quotes.book.bind_shared_policy(policy)
    }
    pub fn state(&self) -> Result<&Runtime> {
        self.market.available()?;
        self.market.runtime.available()?;
        Ok(&self.market.runtime)
    }
    pub fn pending_events(&self) -> usize {
        self.market.pending() + self.quotes.buffer.pending()
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
            PendingKind::Quote { key } => Kind::Quote {
                observation: self
                    .quotes
                    .buffer
                    .first_releasable()
                    .filter(|event| &event.key == key)
                    .ok_or_else(|| Error::Conflict("pending quote missing".into()))?,
            },
            PendingKind::Completed {
                interval_ns,
                index,
                available_at_ns,
            } => Kind::Completed {
                interval_ns: *interval_ns,
                available_at_ns: *available_at_ns,
                bar: self
                    .market
                    .runtime
                    .timeframe(*interval_ns)?
                    .completed()
                    .get(*index)
                    .ok_or_else(|| Error::Conflict("pending completed bar missing".into()))?,
            },
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
        if evaluated_at_ns
            < self
                .market
                .newest_receipt_ns
                .max(self.quotes.newest_receipt_ns)
        {
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
            self.quotes.buffer.begin_release(watermark_ns)?;
            let trade = self.market.buffer.first_releasable();
            let quote = self.quotes.buffer.first_releasable();
            let quote_first = quote.is_some_and(|q| {
                trade.is_none_or(|t| {
                    (q.sip.ns, q.key.sequence, &q.key) < (t.sip.ns, t.key.sequence, &t.key)
                })
            });
            let cutoff = if quote_first {
                quote.unwrap().sip.ns
            } else {
                trade.map_or(watermark_ns, |event| event.sip.ns)
            };
            if self.completed.is_empty() {
                if let Some(close_ns) = self
                    .market
                    .runtime
                    .next_completion_ns()
                    .filter(|at| *at <= cutoff)
                {
                    let before: Vec<_> = self
                        .market
                        .runtime
                        .series()
                        .map(|(interval, series)| (interval, series.completed().len()))
                        .collect();
                    self.market.runtime.advance(close_ns, evaluated_at_ns)?;
                    // Larger timeframe first at a shared close. The candidate's
                    // completed 5s MACD must precede its 1s preview/evaluation.
                    for (interval, index) in before.into_iter().rev() {
                        if self.market.runtime.timeframe(interval)?.completed().len() > index {
                            self.completed.push_back((interval, index, evaluated_at_ns));
                        }
                    }
                }
            }
            let kind =
                if let Some((interval_ns, index, available_at_ns)) = self.completed.pop_front() {
                    PendingKind::Completed {
                        interval_ns,
                        index,
                        available_at_ns,
                    }
                } else if quote_first {
                    self.market.runtime.advance(cutoff, evaluated_at_ns)?;
                    PendingKind::Quote {
                        key: self.quotes.apply_first()?,
                    }
                } else if let Some(event) = self.market.buffer.first_releasable() {
                    let eligible =
                        *self.market.eligibility.get(&event.key).ok_or_else(|| {
                            Error::Unready("causal trade eligibility missing".into())
                        })?;
                    self.market.runtime.advance(event.sip.ns, evaluated_at_ns)?;
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
                } else {
                    self.market.runtime.advance(watermark_ns, evaluated_at_ns)?;
                    return Ok(false);
                };
            let scope = self.market.scope();
            let id = crate::content_hash(&(
                "causal-market-boundary-v3",
                &self.run_id,
                (scope.provider, scope.instrument, scope.session),
                self.market.runtime.configuration_hash(),
                next_sequence,
                evaluated_at_ns,
                match &kind {
                    PendingKind::Quote { key } => crate::content_hash(&("quote", key))?,
                    PendingKind::Completed {
                        interval_ns,
                        index,
                        available_at_ns,
                    } => crate::content_hash(&(
                        interval_ns,
                        available_at_ns,
                        &self.market.runtime.timeframe(*interval_ns)?.completed()[*index],
                    ))?,
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
        if let PendingKind::Quote { key } = &pending.kind {
            self.quotes.buffer.acknowledge_first(key)?;
        }
        self.pending = None;
        Ok(())
    }
}

#[cfg(test)]
#[path = "scheduler_tests.rs"]
mod tests;
