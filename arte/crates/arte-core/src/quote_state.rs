//! Latest source quote, shared by live and replay. Not an execution permission.
use crate::{
    event_order::Scope,
    events::{Decimal, EventKind, Observation, Payload},
    Error, Result,
};
pub mod eligibility;
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
    policy: Option<std::sync::Arc<eligibility::Pinned>>,
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
            policy: None,
        })
    }
    /// Policy identity is immutable for this book. A new version needs a new owner.
    pub fn bind_policy(&mut self, policy: eligibility::Pinned) -> Result<()> {
        self.bind_shared_policy(std::sync::Arc::new(policy))
    }
    pub fn bind_shared_policy(
        &mut self,
        policy: std::sync::Arc<eligibility::Pinned>,
    ) -> Result<()> {
        if policy.provider() != self.scope.provider
            || self
                .policy
                .as_ref()
                .is_some_and(|old| old.hash() != policy.hash())
        {
            return Err(Error::Conflict(
                "quote eligibility policy binding differs".into(),
            ));
        }
        self.policy = Some(policy);
        Ok(())
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
        self.policy
            .as_ref()
            .ok_or_else(|| Error::Unready("quote eligibility policy missing".into()))?
            .require(quote, now_ns)?;
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
    fn policy() -> eligibility::Policy {
        eligibility::Policy {
            provider: 1,
            valid_from_ns: 100,
            valid_to_ns: 200,
            available_at_ns: 100,
            source_manifest_hash: "a".repeat(64),
            allowed_conditions: [1].into(),
            allowed_indicators: [2].into(),
            allow_empty_conditions: true,
            allow_empty_indicators: true,
        }
    }
    fn pinned(p: eligibility::Policy) -> eligibility::Pinned {
        let hash = crate::content_hash(&p).unwrap();
        eligibility::Pinned::new(p, &hash).unwrap()
    }
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
        book.bind_policy(pinned(policy())).unwrap();
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
    #[test]
    fn quote_policy_is_required_and_cannot_be_replaced_silently() {
        let mut book = Book::new(Scope {
            provider: 1,
            instrument: 1,
            session: 20260915,
        })
        .unwrap();
        book.observe(&quote(1, 100, 100, 1001)).unwrap();
        assert!(book.require_executable(101, 10).is_err());
        book.bind_policy(pinned(policy())).unwrap();
        book.bind_policy(pinned(policy())).unwrap();
        assert!(book.require_executable(101, 10).is_ok());
        let mut changed = policy();
        changed.allow_empty_conditions = false;
        assert!(book.bind_policy(pinned(changed)).is_err());
        let mut foreign = policy();
        foreign.provider = 2;
        assert!(book.bind_policy(pinned(foreign)).is_err());
        assert!(eligibility::Pinned::new(policy(), &"0".repeat(64)).is_err());
        let mut disallowed = quote(2, 105, 100, 1001);
        if let Payload::Quote { conditions, .. } = &mut disallowed.payload {
            conditions.push(9);
        }
        book.observe(&disallowed).unwrap();
        assert_eq!(book.latest().unwrap().key.sequence, 2);
        assert!(book.require_executable(106, 10).is_err());
    }
    #[test]
    fn policy_checks_unknown_codes_effective_interval_and_causal_availability() {
        let p = pinned(policy());
        let mut event = quote(1, 100, 100, 1001);
        if let Payload::Quote {
            conditions,
            indicators,
            ..
        } = &mut event.payload
        {
            *conditions = vec![1];
            *indicators = vec![2];
        }
        assert!(p.require(&event, 101).is_ok());
        for change in 0..5 {
            let mut event = event.clone();
            match change {
                0 => {
                    if let Payload::Quote { conditions, .. } = &mut event.payload {
                        conditions.push(9);
                    }
                }
                1 => {
                    if let Payload::Quote { indicators, .. } = &mut event.payload {
                        indicators.push(9);
                    }
                }
                2 => event.sip.ns = 99,
                3 => event.sip.ns = 200,
                _ => event.key.provider = 2,
            }
            assert!(p.require(&event, 201).is_err());
        }
        assert!(p.require(&event, 99).is_err());
        let mut strict = policy();
        strict.allow_empty_conditions = false;
        strict.allow_empty_indicators = false;
        assert!(pinned(strict)
            .require(&quote(1, 100, 100, 1001), 101)
            .is_err());
    }
}
