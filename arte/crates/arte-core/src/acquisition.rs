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
    /// Accepted identical acquisitions coalesced by batch preparation.
    pub deduplicated_rows: u64,
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
        self.validate_inner(acknowledged, true)
    }
    /// Progress validation only. Never grants a VerifiedCertificate or coverage.
    pub fn validate_prefix(&self, acknowledged: &BTreeSet<String>) -> Result<()> {
        self.validate_inner(acknowledged, false)
    }
    fn validate_inner(&self, acknowledged: &BTreeSet<String>, complete: bool) -> Result<()> {
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
                || page.deduplicated_rows > page.accepted_rows
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
        if complete && expected.is_some() {
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
/// Only the streaming batch verifier can construct catalog publication authority.
pub struct VerifiedCertificate(Certificate);
pub struct Verifier {
    certificate: Certificate,
    page: usize,
    batch: usize,
    rows: u64,
    failed: bool,
}
impl Verifier {
    pub fn new(certificate: Certificate) -> Result<Self> {
        let references = certificate
            .pages
            .iter()
            .flat_map(|p| p.batches.iter().cloned())
            .collect();
        // Syntactic/pagination validation first. References become actual evidence
        // only as readback-verified batches arrive below.
        certificate.validate(&references)?;
        let mut verifier = Self {
            certificate,
            page: 0,
            batch: 0,
            rows: 0,
            failed: false,
        };
        verifier.advance_empty();
        Ok(verifier)
    }
    fn advance_empty(&mut self) {
        while self.page < self.certificate.pages.len()
            && self.certificate.pages[self.page].batches.is_empty()
        {
            self.page += 1;
        }
    }
    pub fn next_batch(&self) -> Option<&str> {
        self.certificate
            .pages
            .get(self.page)
            .and_then(|p| p.batches.get(self.batch))
            .map(String::as_str)
    }
    /// Feed one complete batch read back from persistence. No whole-session buffer.
    pub fn observe(&mut self, batch: &crate::event_storage::Batch) -> Result<()> {
        if self.failed {
            return Err(Error::Unready("coverage verifier failed".into()));
        }
        let result = self.observe_inner(batch);
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    fn observe_inner(&mut self, batch: &crate::event_storage::Batch) -> Result<()> {
        if self.next_batch() != Some(batch.id()?.as_str()) {
            return Err(Error::Conflict(
                "coverage batch order or identity differs".into(),
            ));
        }
        batch.verify_readback(batch.payloads(), batch.observations())?;
        let authority = &self.certificate.authority;
        let page = &self.certificate.pages[self.page];
        for event in batch.observations() {
            if event.key.provider != authority.provider
                || event.key.instrument != authority.instrument
                || event.key.kind != authority.kind
                || event.sip.ns < self.certificate.interval.start
                || event.sip.ns >= self.certificate.interval.end
                || event.receipt.is_some()
                || event.available_at_ns != page.acquired_at_ns
            {
                return Err(Error::Conflict(
                    "persisted event outside acquisition authority or clock".into(),
                ));
            }
        }
        self.rows = self
            .rows
            .checked_add(batch.observations().len() as u64)
            .ok_or_else(|| Error::Capacity("coverage row count overflow".into()))?;
        self.batch += 1;
        if self.batch == page.batches.len() {
            if self.rows != page.accepted_rows - page.deduplicated_rows {
                return Err(Error::Conflict(
                    "persisted acquisition count differs".into(),
                ));
            }
            self.page += 1;
            self.batch = 0;
            self.rows = 0;
            self.advance_empty();
        }
        Ok(())
    }
    pub fn finish(self) -> Result<VerifiedCertificate> {
        if self.failed || self.page != self.certificate.pages.len() {
            return Err(Error::Unready(
                "coverage batch verification incomplete".into(),
            ));
        }
        Ok(VerifiedCertificate(self.certificate))
    }
}
impl VerifiedCertificate {
    pub fn certificate(&self) -> &Certificate {
        &self.0
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
    pub fn publish(&mut self, verified: VerifiedCertificate) -> Result<String> {
        let certificate = verified.0;
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
    /// Merge only another verified catalog. Capacity failure leaves self unchanged.
    pub fn merge(&mut self, other: Catalog) -> Result<()> {
        let added = other.ids.difference(&self.ids).count();
        if added > self.maximum - self.entries.len() {
            return Err(Error::Capacity("merged coverage catalog full".into()));
        }
        for certificate in other.entries {
            let id = certificate.id()?;
            if self.ids.insert(id) {
                self.entries.push(certificate);
            }
        }
        Ok(())
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
                deduplicated_rows: 0,
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
    fn catalog_merge_is_deduplicated_and_capacity_failure_is_atomic() {
        let mut c = certificate();
        c.pages[0].source_rows = 0;
        c.pages[0].accepted_rows = 0;
        c.pages[0].batches.clear();
        let mut target = Catalog::new(1).unwrap();
        target
            .publish(Verifier::new(c.clone()).unwrap().finish().unwrap())
            .unwrap();
        let mut duplicate = Catalog::new(1).unwrap();
        duplicate
            .publish(Verifier::new(c.clone()).unwrap().finish().unwrap())
            .unwrap();
        target.merge(duplicate).unwrap();
        assert_eq!(target.entries.len(), 1);
        c.authority.instrument = 2;
        let other_authority = c.authority.clone();
        let mut additional = Catalog::new(1).unwrap();
        additional
            .publish(Verifier::new(c).unwrap().finish().unwrap())
            .unwrap();
        assert!(matches!(target.merge(additional), Err(Error::Capacity(_))));
        assert_eq!(target.entries.len(), 1);
        assert_eq!(
            target
                .missing(&other_authority, Interval { start: 10, end: 20 }, 50)
                .unwrap(),
            vec![Interval { start: 10, end: 20 }]
        );
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
        let mut c = certificate();
        c.pages[0].source_rows = 0;
        c.pages[0].accepted_rows = 0;
        c.pages[0].batches.clear();
        let mut catalog = Catalog::new(2).unwrap();
        let authority = c.authority.clone();
        catalog
            .publish(Verifier::new(c).unwrap().finish().unwrap())
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
    fn persisted() -> crate::event_storage::Batch {
        use crate::events::*;
        crate::event_storage::Batch::prepare(&[Observation {
            key: EventKey {
                provider: 1,
                instrument: 1,
                session: 20260915,
                kind: EventKind::Trade,
                sequence: 1,
            },
            payload: Payload::Trade {
                price: Decimal::parse("10").unwrap(),
                size: Decimal::parse("1").unwrap(),
                exchange: 1,
                trade_id: "t".into(),
                trf: None,
                conditions: vec![],
                correction: None,
            },
            sip: SourceTime {
                ns: 15,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: 30,
            receipt: None,
        }])
        .unwrap()
    }
    #[test]
    fn actual_batch_scope_and_counts_are_required_before_publication() {
        let batch = persisted();
        let mut c = certificate();
        c.pages[0].batches = vec![batch.id().unwrap()];
        assert!(Verifier::new(c.clone()).unwrap().finish().is_err());
        let mut v = Verifier::new(c.clone()).unwrap();
        v.observe(&batch).unwrap();
        v.finish().unwrap();
        c.authority.instrument = 2;
        let mut v = Verifier::new(c).unwrap();
        assert!(v.observe(&batch).is_err());
        assert!(v.finish().is_err());
    }
    #[test]
    fn row_count_mismatch_latches_verification_failure() {
        let batch = persisted();
        let mut c = certificate();
        c.pages[0].batches = vec![batch.id().unwrap()];
        c.pages[0].source_rows = 2;
        c.pages[0].accepted_rows = 2;
        let mut v = Verifier::new(c.clone()).unwrap();
        assert!(v.observe(&batch).is_err());
        assert!(v.observe(&batch).is_err());
        c.pages[0].deduplicated_rows = 1;
        let mut v = Verifier::new(c).unwrap();
        v.observe(&batch).unwrap();
        v.finish().unwrap();
    }
}
