//! Latest source quote, shared by live and replay. Not an execution permission.
use crate::{
    event_order::Scope,
    events::{Decimal, EventKind, Observation, Payload},
    Error, Result,
};
#[derive(Debug, PartialEq, Eq)]
pub enum Update {
    Applied,
    Duplicate,
    Older,
}
pub struct Book {
    scope: Scope,
    latest: Option<Observation>,
    failed: bool,
}
impl Book {
    pub fn new(scope: Scope) -> Result<Self> {
        if scope.provider == 0
            || scope.instrument == 0
            || !(19000101..=29991231).contains(&scope.session)
        {
            return Err(Error::Invalid("quote book scope".into()));
        }
        Ok(Self {
            scope,
            latest: None,
            failed: false,
        })
    }
    pub fn observe(&mut self, event: &Observation) -> Result<Update> {
        if self.failed {
            return Err(Error::Unready("quote book requires recovery".into()));
        }
        event.validate()?;
        if event.key.provider != self.scope.provider
            || event.key.instrument != self.scope.instrument
            || event.key.session != self.scope.session
            || event.key.kind != EventKind::Quote
        {
            return Err(Error::Conflict("quote book source differs".into()));
        }
        if let Some(previous) = &self.latest {
            if previous.key == event.key {
                if previous.payload == event.payload && previous.sip == event.sip {
                    return Ok(Update::Duplicate);
                }
                self.failed = true;
                return Err(Error::Conflict("latest quote identity changed".into()));
            }
            if (event.sip.ns, event.key.sequence) <= (previous.sip.ns, previous.key.sequence) {
                return Ok(Update::Older);
            }
        }
        self.latest = Some(event.clone());
        Ok(Update::Applied)
    }
    /// Raw audit/presentation view includes unusable quotes. Never silently replace
    /// a crossed/empty update with the last attractive price.
    pub fn latest(&self) -> Option<&Observation> {
        self.latest.as_ref()
    }
    pub fn require_executable(&self, now_ns: u64, maximum_age_ns: u64) -> Result<&Observation> {
        if self.failed || maximum_age_ns == 0 {
            return Err(Error::Unready("quote book blocked".into()));
        }
        let quote = self
            .latest
            .as_ref()
            .ok_or_else(|| Error::Unready("quote missing".into()))?;
        for at in [quote.sip.ns, quote.available_at_ns] {
            if now_ns < at || now_ns - at >= maximum_age_ns {
                return Err(Error::Unready("quote clock stale or future".into()));
            }
        }
        let Payload::Quote {
            bid,
            ask,
            bid_size,
            ask_size,
            ..
        } = &quote.payload
        else {
            unreachable!()
        };
        if !bid.positive()
            || !ask.positive()
            || !bid_size.positive()
            || !ask_size.positive()
            || compare(*bid, *ask).is_ge()
        {
            return Err(Error::Unready("quote empty, locked or crossed".into()));
        }
        Ok(quote)
    }
}
fn compare(left: Decimal, right: Decimal) -> std::cmp::Ordering {
    let scale = left.scale.max(right.scale);
    let left = i128::from(left.atoms) * 10_i128.pow(u32::from(scale - left.scale));
    let right = i128::from(right.atoms) * 10_i128.pow(u32::from(scale - right.scale));
    left.cmp(&right)
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::{EventKey, SourceTime};
    fn quote(sequence: u64, at: u64, bid: i64, ask: i64) -> Observation {
        Observation {
            key: EventKey {
                provider: 1,
                instrument: 1,
                session: 20260915,
                kind: EventKind::Quote,
                sequence,
            },
            payload: Payload::Quote {
                bid: Decimal {
                    atoms: bid,
                    scale: 1,
                },
                ask: Decimal {
                    atoms: ask,
                    scale: 2,
                },
                bid_size: Decimal { atoms: 1, scale: 0 },
                ask_size: Decimal { atoms: 1, scale: 0 },
                bid_exchange: 1,
                ask_exchange: 1,
                conditions: vec![],
                indicators: vec![],
            },
            sip: SourceTime {
                ns: at,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: at + 1,
            receipt: None,
        }
    }
    #[test]
    fn duplicates_never_refresh_age_and_crossed_updates_are_not_hidden() {
        let mut book = Book::new(Scope {
            provider: 1,
            instrument: 1,
            session: 20260915,
        })
        .unwrap();
        let mut first = quote(1, 100, 100, 1001);
        assert_eq!(book.observe(&first).unwrap(), Update::Applied);
        book.require_executable(101, 10).unwrap();
        first.available_at_ns = 109;
        assert_eq!(book.observe(&first).unwrap(), Update::Duplicate);
        assert_eq!(book.latest().unwrap().available_at_ns, 101);
        assert!(book.require_executable(110, 10).is_err());
        assert_eq!(
            book.observe(&quote(2, 110, 101, 1001)).unwrap(),
            Update::Applied
        );
        assert!(book.require_executable(111, 10).is_err());
        assert_eq!(
            book.observe(&quote(3, 105, 100, 1001)).unwrap(),
            Update::Older
        );
        assert_eq!(book.latest().unwrap().key.sequence, 2);
        assert!(book.observe(&quote(2, 110, 100, 1001)).is_err());
    }
}
