//! Page normalization precedes any publication. Page failure emits no partial proof.
use crate::event_writer::Publisher;
use crate::massive::{normalize, FetchedPage};
use arte_core::acquisition::{Authority, Page};
use arte_core::coverage::Interval;
use arte_core::event_storage::{Batch, MAX_OBSERVATIONS};
use arte_core::{Error, Result};
use std::{collections::BTreeSet, future::Future};
pub trait Fetcher {
    fn fetch(&mut self, url: &str, path: &str) -> impl Future<Output = Result<FetchedPage>> + Send;
}
impl Fetcher for crate::massive::RestClient {
    async fn fetch(&mut self, url: &str, path: &str) -> Result<FetchedPage> {
        self.fetch_page(url, path).await
    }
}
pub struct Acquisition {
    authority: Authority,
    interval: Interval,
    path: String,
    first_hash: String,
    next: Option<String>,
    pending: Option<PreparedPage>,
    pending_batch: usize,
    pages: Vec<Page>,
    acknowledged: BTreeSet<String>,
    visited: BTreeSet<String>,
    last_sip: Option<u64>,
    maximum_pages: usize,
}
impl Acquisition {
    /// Reference authority must bind this symbol to the instrument over the entire
    /// interval. Split a request at any identity boundary before construction.
    pub fn new(
        authority: Authority,
        interval: Interval,
        symbol: &str,
        maximum_pages: usize,
    ) -> Result<Self> {
        interval.validate()?;
        if authority.provider != 1
            || authority.instrument == 0
            || symbol.is_empty()
            || symbol.len() > 32
            || !symbol
                .bytes()
                .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b".-^".contains(&b))
            || maximum_pages == 0
            || maximum_pages > arte_core::acquisition::MAX_PAGES
        {
            return Err(Error::Invalid("invalid historical acquisition plan".into()));
        }
        let channel = match authority.kind {
            arte_core::events::EventKind::Trade => "trades",
            arte_core::events::EventKind::Quote => "quotes",
        };
        let path = format!("/v3/{channel}/{symbol}");
        let mut url = reqwest::Url::parse(&format!("https://api.massive.com{path}"))
            .map_err(|_| Error::Invalid("invalid historical URL".into()))?;
        url.query_pairs_mut().extend_pairs([
            ("timestamp.gte", interval.start.to_string()),
            ("timestamp.lt", interval.end.to_string()),
            ("order", "asc".into()),
            ("sort", "timestamp".into()),
            ("limit", "50000".into()),
        ]);
        let url = crate::massive::validate_page_url(url.as_str(), &path)?.to_string();
        let first_hash = arte_core::content_hash(&url)?;
        Ok(Self {
            authority,
            interval,
            path,
            first_hash,
            next: Some(url),
            pending: None,
            pending_batch: 0,
            pages: vec![],
            acknowledged: BTreeSet::new(),
            visited: BTreeSet::new(),
            last_sip: None,
            maximum_pages,
        })
    }
    pub fn completed_pages(&self) -> usize {
        self.pages.len()
    }
    pub fn pending_page(&self) -> Option<&PreparedPage> {
        self.pending.as_ref()
    }
    /// One page per step. Exact prepared data survives publication errors/cancelled
    /// futures in this borrowed owner. No implicit request retry or coverage claim.
    pub async fn step(
        &mut self,
        fetcher: &mut impl Fetcher,
        publisher: &mut impl Publisher,
    ) -> Result<bool> {
        let Some(url) = self.next.as_ref() else {
            return Ok(false);
        };
        if self.pending.is_none() {
            if self.pages.len() >= self.maximum_pages {
                return Err(Error::Capacity("historical page budget exceeded".into()));
            }
            let expected = arte_core::content_hash(url)?;
            if self.visited.contains(&expected) {
                return Err(Error::Conflict("historical pagination loop".into()));
            }
            let page = fetcher.fetch(url, &self.path).await?;
            if page.request_hash != expected {
                return Err(Error::Conflict("fetched historical request differs".into()));
            }
            if self
                .pages
                .last()
                .is_some_and(|old| page.acquired_at_ns < old.acquired_at_ns)
            {
                return Err(Error::Invalid("historical acquisition clock rewind".into()));
            }
            if let Some(next) = &page.next_url {
                if crate::massive::validate_page_url(next, &self.path)?.as_str() != next {
                    return Err(Error::Invalid("uncanonical historical cursor".into()));
                }
            }
            self.pending = Some(prepare_page(
                &page,
                &self.authority,
                self.interval,
                self.last_sip,
            )?);
            self.pending_batch = 0;
        }
        let pending = self.pending.as_ref().unwrap();
        while self.pending_batch < pending.batches.len() {
            let batch = &pending.batches[self.pending_batch];
            let id = batch.id()?;
            if publisher.publish(batch).await? != id {
                return Err(Error::Conflict(
                    "historical batch acknowledgment differs".into(),
                ));
            }
            self.acknowledged.insert(id);
            self.pending_batch += 1;
        }
        let pending = self.pending.take().unwrap();
        self.visited.insert(pending.proof.request_hash.clone());
        self.last_sip = pending.last_sip_ns;
        self.next = pending.next_url;
        self.pages.push(pending.proof);
        Ok(true)
    }
    /// Result is an unverified certificate until persisted batches are read back.
    pub fn certificate(&self, published_at_ns: u64) -> Result<arte_core::acquisition::Certificate> {
        if self.next.is_some() || self.pending.is_some() {
            return Err(Error::Unready("historical acquisition incomplete".into()));
        }
        let certificate = arte_core::acquisition::Certificate {
            schema_version: 1,
            authority: self.authority.clone(),
            interval: self.interval,
            first_request_hash: self.first_hash.clone(),
            pages: self.pages.clone(),
            published_at_ns,
        };
        certificate.validate(&self.acknowledged)?;
        Ok(certificate)
    }
}
pub struct PreparedPage {
    pub proof: Page,
    pub batches: Vec<Batch>,
    pub last_sip_ns: Option<u64>,
    pub next_url: Option<String>,
}
pub fn prepare_page(
    page: &FetchedPage,
    authority: &Authority,
    interval: Interval,
    previous_sip_ns: Option<u64>,
) -> Result<PreparedPage> {
    interval.validate()?;
    if authority.provider != 1 || authority.instrument == 0 || page.rows.len() > 50_000 {
        return Err(Error::Invalid(
            "unsupported acquisition scope or page size".into(),
        ));
    }
    let mut events = Vec::with_capacity(page.rows.len());
    let mut last = previous_sip_ns;
    for row in &page.rows {
        let event = normalize(
            row,
            authority.instrument,
            authority.kind,
            false,
            page.acquired_at_ns,
            None,
        )?;
        if event.sip.ns < interval.start
            || event.sip.ns >= interval.end
            || last.is_some_and(|at| event.sip.ns < at)
        {
            return Err(Error::Conflict(
                "REST timestamp order or requested interval mismatch".into(),
            ));
        }
        last = Some(event.sip.ns);
        events.push(event);
    }
    let batches: Vec<_> = events
        .chunks(MAX_OBSERVATIONS)
        .map(Batch::prepare)
        .collect::<Result<_>>()?;
    let persisted = batches
        .iter()
        .map(|b| b.observations().len() as u64)
        .sum::<u64>();
    let count = events.len() as u64;
    let proof = Page {
        request_hash: page.request_hash.clone(),
        response_hash: page.response_hash.clone(),
        next_request_hash: page
            .next_url
            .as_deref()
            .map(|url| arte_core::content_hash(&url))
            .transpose()?,
        acquired_at_ns: page.acquired_at_ns,
        source_rows: count,
        accepted_rows: count,
        rejected_rows: 0,
        deduplicated_rows: count - persisted,
        batches: batches.iter().map(Batch::id).collect::<Result<_>>()?,
        identity_checked: true,
        ordering_checked: true,
        interval_checked: true,
    };
    Ok(PreparedPage {
        proof,
        batches,
        last_sip_ns: last,
        next_url: page.next_url.clone(),
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::events::EventKind;
    fn inputs() -> (FetchedPage, Authority, Interval) {
        (
            FetchedPage {
                rows: vec![
                    serde_json::json!({"price":10,"size":1,"exchange":1,"id":"t","sequence_number":1,"sip_timestamp":15}),
                ],
                next_url: None,
                request_hash: "a".repeat(64),
                response_hash: "b".repeat(64),
                acquired_at_ns: 30,
            },
            Authority {
                provider: 1,
                instrument: 1,
                kind: EventKind::Trade,
                source_revision: "r".into(),
                contract_hash: "c".repeat(64),
                capabilities_hash: "d".repeat(64),
            },
            Interval { start: 10, end: 20 },
        )
    }
    #[test]
    fn duplicate_rows_reconcile_without_fabricating_receive_time() {
        let (mut page, authority, interval) = inputs();
        page.rows.push(page.rows[0].clone());
        let prepared = prepare_page(&page, &authority, interval, None).unwrap();
        assert_eq!(prepared.proof.deduplicated_rows, 1);
        assert_eq!(prepared.batches[0].observations().len(), 1);
        assert!(prepared.batches[0].observations()[0].receipt.is_none());
    }
    #[test]
    fn equal_timestamp_page_boundary_allowed_but_rewind_rejected() {
        let (page, authority, interval) = inputs();
        assert!(prepare_page(&page, &authority, interval, Some(15)).is_ok());
        assert!(prepare_page(&page, &authority, interval, Some(16)).is_err());
        assert!(prepare_page(&page, &authority, Interval { start: 10, end: 15 }, None).is_err());
    }
    struct Source {
        calls: usize,
    }
    impl Fetcher for Source {
        async fn fetch(&mut self, url: &str, path: &str) -> Result<FetchedPage> {
            let (mut page, _, _) = inputs();
            page.request_hash = arte_core::content_hash(&url)?;
            page.acquired_at_ns += self.calls as u64;
            page.next_url = if self.calls == 0 {
                Some(format!("https://api.massive.com{path}?cursor=second"))
            } else {
                None
            };
            self.calls += 1;
            Ok(page)
        }
    }
    struct Sink {
        fail: bool,
        batches: std::collections::BTreeMap<String, Batch>,
    }
    impl Publisher for Sink {
        async fn publish(&mut self, batch: &Batch) -> Result<String> {
            let id = batch.id()?;
            self.batches.insert(id.clone(), batch.clone());
            if self.fail {
                self.fail = false;
                return Err(Error::Unready("ambiguous publication".into()));
            }
            Ok(id)
        }
    }
    #[tokio::test]
    async fn retry_preserves_page_and_certifies_only_after_final_page_readback() {
        let (_, authority, interval) = inputs();
        let mut run = Acquisition::new(authority, interval, "AAPL", 3).unwrap();
        let mut source = Source { calls: 0 };
        let mut sink = Sink {
            fail: true,
            batches: Default::default(),
        };
        assert!(run.step(&mut source, &mut sink).await.is_err());
        assert_eq!(run.completed_pages(), 0);
        assert!(run.pending_page().is_some());
        assert!(run.certificate(40).is_err());
        run.step(&mut source, &mut sink).await.unwrap();
        assert_eq!(source.calls, 1);
        assert_eq!(run.completed_pages(), 1);
        assert!(run.certificate(40).is_err());
        run.step(&mut source, &mut sink).await.unwrap();
        assert_eq!(source.calls, 2);
        assert!(!run.step(&mut source, &mut sink).await.unwrap());
        let mut verifier =
            arte_core::acquisition::Verifier::new(run.certificate(40).unwrap()).unwrap();
        while let Some(id) = verifier.next_batch() {
            let batch = sink.batches.get(id).unwrap();
            verifier.observe(batch).unwrap();
        }
        verifier.finish().unwrap();
    }
}
