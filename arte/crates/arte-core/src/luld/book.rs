//! One bounded in-memory band projection per provider/instrument/session.
use super::Evidence;
use crate::{event_order::Scope, Error, Result};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Update {
    Applied,
    Duplicate,
    Older,
}
pub struct Book {
    scope: Scope,
    latest: Option<Evidence>,
    failed: bool,
}
impl Book {
    pub fn new(scope: Scope) -> Result<Self> {
        if scope.provider == 0
            || scope.instrument == 0
            || !(19000101..=29991231).contains(&scope.session)
        {
            return Err(Error::Invalid("LULD book scope".into()));
        }
        Ok(Self {
            scope,
            latest: None,
            failed: false,
        })
    }
    /// Adapter must supply original provider effective time. Repeated delivery is
    /// not a band update. Ambiguous same-time changes require explicit recovery;
    /// this contract does not invent provider ordering or correction semantics.
    pub fn observe(&mut self, evidence: &Evidence, received_at_ns: u64) -> Result<Update> {
        if self.failed {
            return Err(Error::Unready("LULD book requires recovery".into()));
        }
        let result = (|| {
            evidence.require(self.scope, evidence.scale, received_at_ns, u64::MAX)?;
            if let Some(previous) = &self.latest {
                if evidence.effective_at_ns < previous.effective_at_ns {
                    return Ok(Update::Older);
                }
                if evidence.effective_at_ns == previous.effective_at_ns {
                    if evidence.lower != previous.lower
                        || evidence.upper != previous.upper
                        || evidence.scale != previous.scale
                    {
                        return Err(Error::Conflict("same-time LULD geometry changed".into()));
                    }
                    return Ok(Update::Duplicate);
                }
            }
            self.latest = Some(evidence.clone());
            Ok(Update::Applied)
        })();
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    /// Audit only. Includes the last observation after invalidation; never use it
    /// as permission. Executable consumers must call require_current every time.
    pub fn latest(&self) -> Option<&Evidence> {
        self.latest.as_ref()
    }
    pub fn invalidate(&mut self) {
        self.failed = true;
    }
    pub fn require_current(
        &self,
        now_ns: u64,
        scale: u8,
        maximum_age_ns: u64,
    ) -> Result<&Evidence> {
        if self.failed {
            return Err(Error::Unready("LULD book requires recovery".into()));
        }
        let evidence = self
            .latest
            .as_ref()
            .ok_or_else(|| Error::Unready("LULD band missing".into()))?;
        evidence.require(self.scope, scale, now_ns, maximum_age_ns)?;
        Ok(evidence)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (Book, Evidence) {
        let scope = Scope {
            provider: 1,
            instrument: 1,
            session: 20260915,
        };
        (
            Book::new(scope).unwrap(),
            Evidence {
                provider: 1,
                instrument: 1,
                session: 20260915,
                lower: 90,
                upper: 110,
                scale: 2,
                effective_at_ns: 10,
                available_at_ns: 11,
                official: true,
            },
        )
    }
    #[test]
    fn duplicate_and_older_do_not_refresh_original_evidence() {
        let (mut book, original) = fixture();
        assert!(book.require_current(11, 2, 10).is_err());
        assert_eq!(book.observe(&original, 11).unwrap(), Update::Applied);
        let mut duplicate = original.clone();
        duplicate.available_at_ns = 20;
        assert_eq!(book.observe(&duplicate, 20).unwrap(), Update::Duplicate);
        assert_eq!(book.require_current(20, 2, 10).unwrap(), &original);
        assert!(book.require_current(21, 2, 10).is_err());
        let mut older = original.clone();
        older.effective_at_ns = 9;
        older.lower = 91;
        assert_eq!(book.observe(&older, 21).unwrap(), Update::Older);
        assert_eq!(book.latest().unwrap(), &original);
        let mut newer = original;
        newer.effective_at_ns = 21;
        newer.available_at_ns = 22;
        assert_eq!(book.observe(&newer, 22).unwrap(), Update::Applied);
        assert_eq!(book.require_current(22, 2, 10).unwrap(), &newer);
    }
    #[test]
    fn conflict_invalid_update_and_disconnect_cannot_fall_back() {
        for case in 0..5 {
            let (mut book, original) = fixture();
            book.observe(&original, 11).unwrap();
            let mut invalid = original.clone();
            match case {
                0 => invalid.lower += 1,
                1 => invalid.instrument = 2,
                2 => invalid.available_at_ns = 100,
                3 => invalid.official = false,
                _ => invalid.lower = 0,
            }
            assert!(book.observe(&invalid, 12).is_err());
            assert_eq!(book.latest().unwrap(), &original);
            assert!(book.require_current(12, 2, 10).is_err());
            assert!(book.observe(&original, 12).is_err());
        }
        let (mut book, original) = fixture();
        book.observe(&original, 11).unwrap();
        book.invalidate();
        assert!(book.require_current(12, 2, 10).is_err());
    }
}
