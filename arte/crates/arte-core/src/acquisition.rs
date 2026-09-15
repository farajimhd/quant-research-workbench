//! Coverage certificates bind a completed pagination chain to acknowledged batches.
//! They certify recorded checks, not absolute completeness of the upstream provider.
use crate::coverage::{missing, Interval};
use crate::events::EventKind;
use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
pub const MAX_PAGES: usize = 100_000;
pub const MAX_BATCH_REFERENCES: usize = 1_000_000;
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Authority {
    pub provider: u16,
    pub instrument: u64,
    pub kind: EventKind,
    pub source_revision: String,
    pub contract_hash: String,
    pub capabilities_hash: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Page {
    /// Hashes exclude authentication data. Persist cursor/request evidence separately.
    pub request_hash: String,
    pub response_hash: String,
    pub next_request_hash: Option<String>,
    pub acquired_at_ns: u64,
    pub source_rows: u64,
    pub accepted_rows: u64,
    pub rejected_rows: u64,
    pub batches: Vec<String>,
    pub identity_checked: bool,
    pub ordering_checked: bool,
    pub interval_checked: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Certificate {
    pub schema_version: u16,
    pub authority: Authority,
    pub interval: Interval,
    pub first_request_hash: String,
    pub pages: Vec<Page>,
    pub published_at_ns: u64,
}
fn hash_ok(h: &str) -> bool {
    h.len() == 64
        && h.bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
impl Certificate {
    /// The acquisition owner supplies actual request/response evidence and only
    /// readback-acknowledged batch IDs. These checks cannot replace those duties.
    pub fn validate(&self, acknowledged: &BTreeSet<String>) -> Result<()> {
        self.interval.validate()?;
        if self.schema_version != 1
            || self.authority.provider == 0
            || self.authority.instrument == 0
            || self.authority.source_revision.is_empty()
            || self.authority.source_revision.len() > 256
            || !hash_ok(&self.authority.contract_hash)
            || !hash_ok(&self.authority.capabilities_hash)
            || !hash_ok(&self.first_request_hash)
            || self.pages.is_empty()
            || self.pages.len() > MAX_PAGES
            || self.published_at_ns == 0
        {
            return Err(Error::Invalid("invalid acquisition certificate".into()));
        }
        let mut expected = Some(self.first_request_hash.as_str());
        let mut seen = BTreeSet::new();
        let mut batch_count = 0usize;
        let mut previous_at = 0;
        for page in &self.pages {
            if expected != Some(page.request_hash.as_str())
                || !hash_ok(&page.request_hash)
                || !hash_ok(&page.response_hash)
                || !seen.insert(&page.request_hash)
                || page
                    .next_request_hash
                    .as_deref()
                    .is_some_and(|h| !hash_ok(h))
            {
                return Err(Error::Conflict(
                    "broken or cyclic pagination evidence".into(),
                ));
            }
            if page.acquired_at_ns == 0
                || page.acquired_at_ns < previous_at
                || page.acquired_at_ns > self.published_at_ns
            {
                return Err(Error::Invalid(
                    "acquisition knowledge clock mismatch".into(),
                ));
            }
            if !page.identity_checked
                || !page.ordering_checked
                || !page.interval_checked
                || page.rejected_rows != 0
                || page.accepted_rows != page.source_rows
            {
                return Err(Error::Unready("acquisition page not fully verified".into()));
            }
            if (page.accepted_rows == 0) != page.batches.is_empty() {
                return Err(Error::Conflict(
                    "page row count and persisted batches disagree".into(),
                ));
            }
            batch_count = batch_count
                .checked_add(page.batches.len())
                .ok_or_else(|| Error::Capacity("batch-reference overflow".into()))?;
            if batch_count > MAX_BATCH_REFERENCES {
                return Err(Error::Capacity("acquisition batch-reference limit".into()));
            }
            let mut page_batches = BTreeSet::new();
            for batch in &page.batches {
                if !hash_ok(batch) || !page_batches.insert(batch) {
                    return Err(Error::Invalid("invalid or duplicate page batch".into()));
                }
                if !acknowledged.contains(batch) {
                    return Err(Error::Unready("acquisition batch not acknowledged".into()));
                }
            }
            expected = page.next_request_hash.as_deref();
            previous_at = page.acquired_at_ns;
        }
        if expected.is_some() {
            return Err(Error::Unready("provider pagination not exhausted".into()));
        }
        Ok(())
    }
    pub fn id(&self) -> Result<String> {
        content_hash(self)
    }
    pub fn empty(&self) -> bool {
        self.pages.iter().all(|p| p.source_rows == 0)
    }
}
/// Validated, bounded in-memory catalog. Persistent loading must revalidate against
/// actual batch readback; deserializing a Certificate alone grants no readiness.
pub struct Catalog {
    entries: Vec<Certificate>,
    ids: BTreeSet<String>,
    maximum: usize,
}
impl Catalog {
    pub fn new(maximum: usize) -> Result<Self> {
        if maximum == 0 {
            return Err(Error::Invalid("zero coverage catalog capacity".into()));
        }
        Ok(Self {
            entries: vec![],
            ids: BTreeSet::new(),
            maximum,
        })
    }
    pub fn publish(
        &mut self,
        certificate: Certificate,
        acknowledged: &BTreeSet<String>,
    ) -> Result<String> {
        certificate.validate(acknowledged)?;
        let id = certificate.id()?;
        if self.ids.contains(&id) {
            return Ok(id);
        }
        if self.entries.len() >= self.maximum {
            return Err(Error::Capacity("coverage catalog full".into()));
        }
        self.ids.insert(id.clone());
        self.entries.push(certificate);
        Ok(id)
    }
    pub fn missing(
        &self,
        authority: &Authority,
        interval: Interval,
        as_of_ns: u64,
    ) -> Result<Vec<Interval>> {
        let certified: Vec<_> = self
            .entries
            .iter()
            .filter(|c| &c.authority == authority && c.published_at_ns <= as_of_ns)
            .map(|c| c.interval)
            .collect();
        missing(interval, &certified)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn certificate() -> Certificate {
        Certificate {
            schema_version: 1,
            authority: Authority {
                provider: 1,
                instrument: 1,
                kind: EventKind::Trade,
                source_revision: "revision".into(),
                contract_hash: "a".repeat(64),
                capabilities_hash: "b".repeat(64),
            },
            interval: Interval { start: 10, end: 20 },
            first_request_hash: "c".repeat(64),
            pages: vec![Page {
                request_hash: "c".repeat(64),
                response_hash: "d".repeat(64),
                next_request_hash: None,
                acquired_at_ns: 30,
                source_rows: 1,
                accepted_rows: 1,
                rejected_rows: 0,
                batches: vec!["e".repeat(64)],
                identity_checked: true,
                ordering_checked: true,
                interval_checked: true,
            }],
            published_at_ns: 40,
        }
    }
    #[test]
    fn acknowledged_data_does_not_certify_unfinished_pagination() {
        let mut c = certificate();
        let ack = BTreeSet::from(["e".repeat(64)]);
        c.validate(&ack).unwrap();
        c.pages[0].next_request_hash = Some("f".repeat(64));
        assert!(c.validate(&ack).is_err());
        c.pages[0].next_request_hash = None;
        c.pages[0].rejected_rows = 1;
        assert!(c.validate(&ack).is_err());
    }
    #[test]
    fn empty_coverage_requires_a_successful_checked_page() {
        let mut c = certificate();
        c.pages[0].source_rows = 0;
        c.pages[0].accepted_rows = 0;
        c.pages[0].batches.clear();
        c.validate(&BTreeSet::new()).unwrap();
        assert!(c.empty());
        c.pages[0].interval_checked = false;
        assert!(c.validate(&BTreeSet::new()).is_err());
    }
    #[test]
    fn coverage_is_revision_channel_and_knowledge_time_scoped() {
        let c = certificate();
        let mut catalog = Catalog::new(2).unwrap();
        let authority = c.authority.clone();
        catalog
            .publish(c, &BTreeSet::from(["e".repeat(64)]))
            .unwrap();
        let request = Interval { start: 10, end: 25 };
        assert_eq!(
            catalog.missing(&authority, request, 39).unwrap(),
            vec![request]
        );
        assert_eq!(
            catalog.missing(&authority, request, 40).unwrap(),
            vec![Interval { start: 20, end: 25 }]
        );
        let mut other = authority.clone();
        other.kind = EventKind::Quote;
        assert_eq!(catalog.missing(&other, request, 40).unwrap(), vec![request]);
        other = authority;
        other.source_revision = "new".into();
        assert_eq!(catalog.missing(&other, request, 40).unwrap(), vec![request]);
    }
}
