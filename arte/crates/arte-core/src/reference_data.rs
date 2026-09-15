//! Pinned point-in-time reference evidence shared by live and historical consumers.
use crate::{content_hash, event_order::Scope, events::Decimal, Error, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreviousClose {
    pub provider: u16,
    pub instrument: u64,
    pub session: u32,
    pub price: Decimal,
    pub available_at_ns: u64,
    pub source_manifest_hash: String,
}
/// Startup/reference authority pins the actual preceding trading session, not
/// calendar-day subtraction. The hash binds the full immutable reference record.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreviousCloseRequirement {
    pub session: u32,
    pub record_hash: String,
}
fn hash_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
impl PreviousCloseRequirement {
    pub fn validate(&self, scope: Scope) -> Result<()> {
        if scope.provider == 0
            || scope.instrument == 0
            || !(19000101..=29991231).contains(&scope.session)
            || !(19000101..scope.session).contains(&self.session)
            || !hash_valid(&self.record_hash)
        {
            return Err(Error::Invalid(
                "previous-close requirement scope or identity".into(),
            ));
        }
        Ok(())
    }
}
impl PreviousClose {
    /// Hash agreement is identity evidence, not proof of a trustworthy importer.
    /// The producer's manifest/coverage validation remains a separate obligation.
    pub fn require(
        &self,
        scope: Scope,
        requirement: &PreviousCloseRequirement,
        now_ns: u64,
    ) -> Result<Decimal> {
        requirement.validate(scope)?;
        if self.provider != scope.provider
            || self.instrument != scope.instrument
            || self.session != requirement.session
            || self.available_at_ns > now_ns
            || !self.price.positive()
            || !hash_valid(&self.source_manifest_hash)
            || content_hash(self)? != requirement.record_hash
        {
            return Err(Error::Unready(
                "previous close missing scoped causal provenance".into(),
            ));
        }
        Ok(self.price)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pinned_previous_session_and_all_record_fields_are_required() {
        let scope = Scope {
            provider: 1,
            instrument: 2,
            session: 20260914,
        };
        // Friday -> Monday: no invented weekend session.
        let record = PreviousClose {
            provider: 1,
            instrument: 2,
            session: 20260911,
            price: Decimal {
                atoms: 1025,
                scale: 2,
            },
            available_at_ns: 100,
            source_manifest_hash: "a".repeat(64),
        };
        let requirement = PreviousCloseRequirement {
            session: record.session,
            record_hash: content_hash(&record).unwrap(),
        };
        assert_eq!(
            record.require(scope, &requirement, 100).unwrap(),
            record.price
        );
        assert!(record.require(scope, &requirement, 99).is_err());
        for case in 0..7 {
            let mut changed = record.clone();
            match case {
                0 => changed.provider = 2,
                1 => changed.instrument = 3,
                2 => changed.session = 20260910,
                3 => changed.price.atoms += 1,
                4 => changed.available_at_ns = 99,
                5 => changed.source_manifest_hash = "b".repeat(64),
                _ => changed.price.scale = 3,
            }
            assert!(changed.require(scope, &requirement, 100).is_err());
        }
        let current = PreviousCloseRequirement {
            session: scope.session,
            ..requirement.clone()
        };
        assert!(record.require(scope, &current, 100).is_err());
        let unpinned = PreviousCloseRequirement {
            record_hash: String::new(),
            ..requirement
        };
        assert!(record.require(scope, &unpinned, 100).is_err());
    }
}
