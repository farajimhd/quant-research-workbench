//! Bounded quote ownership alongside the trade computation owner.
use crate::{
    content_hash,
    event_order::{Buffer, Scope},
    events::{EventKey, Observation},
    quote_state::Book,
    Error, Result,
};
use std::collections::BTreeMap;
pub(super) struct Quotes {
    pub buffer: Buffer,
    pub book: Book,
    applied: BTreeMap<EventKey, String>,
    maximum: usize,
    pub newest_receipt_ns: u64,
}
impl Quotes {
    pub fn new(scope: Scope, pending: usize, maximum: usize, watermark: u64) -> Result<Self> {
        Ok(Self {
            buffer: Buffer::new(scope, pending, watermark)?,
            book: Book::new(scope)?,
            applied: BTreeMap::new(),
            maximum,
            newest_receipt_ns: watermark,
        })
    }
    pub fn enqueue(&mut self, event: &Observation) -> Result<bool> {
        event.validate()?;
        let hash = content_hash(&(&event.key, &event.payload, event.sip))?;
        if let Some(old) = self.applied.get(&event.key) {
            return if old == &hash {
                Ok(false)
            } else {
                Err(Error::Conflict("released quote identity changed".into()))
            };
        }
        let added = self.buffer.push(event)?;
        if added {
            self.newest_receipt_ns = self.newest_receipt_ns.max(event.available_at_ns);
        }
        Ok(added)
    }
    pub fn apply_first(&mut self) -> Result<EventKey> {
        let event = self
            .buffer
            .first_releasable()
            .ok_or_else(|| Error::Unready("pending quote missing".into()))?;
        if self.applied.len() == self.maximum {
            return Err(Error::Capacity(
                "quote identity budget exhausted; no eviction".into(),
            ));
        }
        let hash = content_hash(&(&event.key, &event.payload, event.sip))?;
        if self.book.observe(event)? != crate::quote_state::Update::Applied {
            return Err(Error::Conflict("ordered quote did not advance".into()));
        }
        self.applied.insert(event.key.clone(), hash);
        Ok(event.key.clone())
    }
}
