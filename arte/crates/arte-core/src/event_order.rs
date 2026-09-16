//! Bounded source-time ordering. Watermarks come from an external authority.
//! This component does not infer completeness from silence or sequence gaps.
use crate::{
    events::{EventKey, Observation},
    Error, Result,
};
use std::collections::BTreeMap;
pub mod checkpoint;
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Scope {
    pub provider: u16,
    pub instrument: u64,
    pub session: u32,
}
pub struct Buffer {
    scope: Scope,
    pending: BTreeMap<(u64, u64, EventKey), Observation>,
    identities: BTreeMap<EventKey, (u64, u64, EventKey)>,
    maximum: usize,
    watermark_ns: u64,
    failed: bool,
}
impl Buffer {
    pub fn new(scope: Scope, maximum: usize, watermark_ns: u64) -> Result<Self> {
        if scope.provider == 0
            || scope.instrument == 0
            || !(19000101..=29991231).contains(&scope.session)
            || maximum == 0
            || maximum > 1_000_000
        {
            return Err(Error::Invalid("event ordering scope or capacity".into()));
        }
        Ok(Self {
            scope,
            pending: BTreeMap::new(),
            identities: BTreeMap::new(),
            maximum,
            watermark_ns,
            failed: false,
        })
    }
    /// Original receipt remains attached. Pending retransmissions are coalesced;
    /// already-released duplicates must be recognized by the session identity owner.
    pub fn push(&mut self, event: &Observation) -> Result<bool> {
        if self.failed {
            return Err(Error::Unready("event ordering requires recovery".into()));
        }
        event.validate()?;
        if event.key.provider != self.scope.provider
            || event.key.instrument != self.scope.instrument
            || event.key.session != self.scope.session
        {
            return Err(Error::Conflict("event ordering scope mismatch".into()));
        }
        if let Some(order) = self.identities.get(&event.key) {
            let previous = &self.pending[order];
            if previous.payload == event.payload && previous.sip == event.sip {
                return Ok(false);
            }
            self.failed = true;
            return Err(Error::Conflict("pending source identity changed".into()));
        }
        if event.sip.ns < self.watermark_ns {
            self.failed = true;
            return Err(Error::Unready(
                "event arrived behind certified release boundary".into(),
            ));
        }
        if self.pending.len() == self.maximum {
            self.failed = true;
            return Err(Error::Capacity(
                "event ordering buffer full; input not admitted".into(),
            ));
        }
        let order = (event.sip.ns, event.key.sequence, event.key.clone());
        self.identities.insert(event.key.clone(), order.clone());
        self.pending.insert(order, event.clone());
        Ok(true)
    }
    /// Release strictly before watermark, preserving half-open bar boundaries.
    /// The callback acknowledges application. On failure the event and all later
    /// events remain queued, and retry resumes the same prefix. No async I/O here.
    pub fn release(
        &mut self,
        watermark_ns: u64,
        mut apply: impl FnMut(&Observation) -> Result<()>,
    ) -> Result<usize> {
        self.begin_release(watermark_ns)?;
        let mut count = 0;
        while let Some(event) = self.first_releasable() {
            apply(event)?;
            let key = event.key.clone();
            self.acknowledge_first(&key)?;
            count += 1;
        }
        Ok(count)
    }
    pub(crate) fn begin_release(&mut self, watermark_ns: u64) -> Result<()> {
        if self.failed {
            return Err(Error::Unready("event ordering requires recovery".into()));
        }
        if watermark_ns < self.watermark_ns {
            return Err(Error::Invalid("event watermark regressed".into()));
        }
        self.watermark_ns = watermark_ns;
        Ok(())
    }
    pub(crate) fn first_releasable(&self) -> Option<&Observation> {
        self.pending
            .first_key_value()
            .filter(|(order, _)| order.0 < self.watermark_ns)
            .map(|(_, event)| event)
    }
    pub(crate) fn acknowledge_first(&mut self, key: &EventKey) -> Result<()> {
        if self.failed
            || self
                .first_releasable()
                .is_none_or(|event| &event.key != key)
        {
            return Err(Error::Conflict(
                "ordered acknowledgment differs from next event".into(),
            ));
        }
        let (_, event) = self.pending.pop_first().unwrap();
        self.identities.remove(&event.key);
        Ok(())
    }
    pub fn pending(&self) -> usize {
        self.pending.len()
    }
    pub(crate) fn pending_events(&self) -> impl Iterator<Item = &Observation> {
        self.pending.values()
    }
    pub(crate) fn maximum(&self) -> usize {
        self.maximum
    }
    pub fn watermark_ns(&self) -> u64 {
        self.watermark_ns
    }
    pub fn failed(&self) -> bool {
        self.failed
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::{Decimal, EventKind, Payload, SourceTime};
    fn event(at: u64, sequence: u64) -> Observation {
        Observation {
            key: EventKey {
                provider: 1,
                instrument: 1,
                session: 20260915,
                kind: EventKind::Trade,
                sequence,
            },
            payload: Payload::Trade {
                price: Decimal {
                    atoms: 10,
                    scale: 0,
                },
                size: Decimal { atoms: 1, scale: 0 },
                exchange: 1,
                trade_id: sequence.to_string(),
                trf: None,
                conditions: vec![],
                correction: None,
            },
            sip: SourceTime {
                ns: at,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: 100,
            receipt: None,
        }
    }
    fn buffer(maximum: usize) -> Buffer {
        Buffer::new(
            Scope {
                provider: 1,
                instrument: 1,
                session: 20260915,
            },
            maximum,
            0,
        )
        .unwrap()
    }
    #[test]
    fn deterministic_order_and_failed_consumer_preserve_remaining_prefix() {
        let mut buffer = buffer(4);
        for (at, seq) in [(20, 3), (10, 2), (10, 1), (30, 4)] {
            buffer.push(&event(at, seq)).unwrap();
        }
        assert!(!buffer.push(&event(10, 1)).unwrap());
        let mut received = vec![];
        assert!(buffer
            .release(30, |event| {
                if event.key.sequence == 2 {
                    return Err(Error::Unready("consumer blocked".into()));
                }
                received.push(event.key.sequence);
                Ok(())
            })
            .is_err());
        assert_eq!(received, vec![1]);
        assert_eq!(buffer.pending(), 3);
        buffer
            .release(30, |event| {
                received.push(event.key.sequence);
                Ok(())
            })
            .unwrap();
        assert_eq!(received, vec![1, 2, 3]);
        assert_eq!(buffer.pending(), 1);
        buffer.release(31, |_| Ok(())).unwrap();
        assert_eq!(buffer.pending(), 0);
    }
    #[test]
    fn late_input_and_capacity_exhaustion_fail_without_dropping_pending() {
        let mut late = buffer(2);
        late.release(20, |_| Ok(())).unwrap();
        assert!(late.push(&event(19, 1)).is_err());
        assert!(late.failed());
        let mut full = buffer(1);
        full.push(&event(10, 1)).unwrap();
        assert!(full.push(&event(11, 2)).is_err());
        assert_eq!(full.pending(), 1);
        assert!(full.release(20, |_| Ok(())).is_err());
    }
}
