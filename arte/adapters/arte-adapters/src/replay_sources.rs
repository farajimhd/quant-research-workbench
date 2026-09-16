//! Certified REST source loading, before any historical clock projection.
//! Observations retain acquisition clocks and must not be passed off as live data.
use arte_core::{
    acquisition::{Certificate, VerifiedCertificate, Verifier},
    event_storage::Batch,
    events::Observation,
    Error, Result,
};
use futures_util::{stream, StreamExt, TryStreamExt};
use std::future::Future;

pub struct Limits {
    pub maximum_batches: usize,
    pub maximum_events: usize,
    pub maximum_bytes: usize,
    pub concurrency: usize,
}
pub struct Source {
    certificate: VerifiedCertificate,
    observations: Vec<Observation>,
}
impl Source {
    pub fn certificate(&self) -> &Certificate {
        self.certificate.certificate()
    }
    pub fn observations(&self) -> &[Observation] {
        &self.observations
    }
}
pub trait Reader {
    fn batch(&self, id: &str) -> impl Future<Output = Result<Batch>> + Send;
}
impl Reader for crate::clickhouse::ClickHouse {
    async fn batch(&self, id: &str) -> Result<Batch> {
        self.load_event_batch(id).await
    }
}
/// Fetch concurrently but verify in certified pagination order. No partial source
/// is returned on failure or cancellation. This does not merge source revisions,
/// infer trade eligibility, certify upstream completeness or fabricate clocks.
pub async fn load(
    reader: &impl Reader,
    certificate: Certificate,
    expected_id: &str,
    as_of_ns: u64,
    limits: Limits,
) -> Result<Source> {
    if limits.maximum_batches == 0
        || limits.maximum_batches > 1_000_000
        || limits.maximum_events == 0
        || limits.maximum_events > 10_000_000
        || limits.maximum_bytes == 0
        || limits.maximum_bytes > 1024 * 1024 * 1024
        || limits.concurrency == 0
        || limits.concurrency > 64
    {
        return Err(Error::Capacity("replay source limits".into()));
    }
    if certificate.id()? != expected_id || certificate.published_at_ns > as_of_ns {
        return Err(Error::Conflict(
            "replay source identity or knowledge cutoff".into(),
        ));
    }
    let mut verifier = Verifier::new(certificate.clone())?;
    let count = certificate
        .pages
        .iter()
        .try_fold(0usize, |n, page| n.checked_add(page.batches.len()))
        .ok_or_else(|| Error::Capacity("replay batch count overflow".into()))?;
    if count > limits.maximum_batches {
        return Err(Error::Capacity("replay source batch count".into()));
    }
    let rows = certificate
        .pages
        .iter()
        .try_fold(0u64, |n, p| {
            n.checked_add(p.accepted_rows - p.deduplicated_rows)
        })
        .ok_or_else(|| Error::Capacity("replay source row count overflow".into()))?;
    if rows > limits.maximum_events as u64 {
        return Err(Error::Capacity("replay source event count".into()));
    }
    let mut batches = stream::iter(certificate.pages.iter().flat_map(|p| &p.batches))
        .map(|id| reader.batch(id))
        .buffered(limits.concurrency);
    let mut observations = Vec::new();
    let mut bytes = 0usize;
    while let Some(batch) = batches.try_next().await? {
        verifier.observe(&batch)?;
        let events = batch.hydrate()?;
        for event in events {
            let size = serde_json::to_vec(&event)
                .map_err(|e| Error::Serialization(e.to_string()))?
                .len();
            bytes = bytes
                .checked_add(size)
                .ok_or_else(|| Error::Capacity("replay source byte overflow".into()))?;
            if bytes > limits.maximum_bytes || observations.len() == limits.maximum_events {
                return Err(Error::Capacity(
                    "replay source retained input budget".into(),
                ));
            }
            observations.push(event);
        }
    }
    Ok(Source {
        certificate: verifier.finish()?,
        observations,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::{
        acquisition::{Authority, Page},
        coverage::Interval,
        events::*,
    };
    use std::{
        collections::BTreeMap,
        sync::atomic::{AtomicUsize, Ordering},
    };
    struct Memory {
        rows: BTreeMap<String, Vec<Observation>>,
        calls: AtomicUsize,
        active: AtomicUsize,
        peak: AtomicUsize,
    }
    impl Reader for Memory {
        async fn batch(&self, id: &str) -> Result<Batch> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            let active = self.active.fetch_add(1, Ordering::SeqCst) + 1;
            self.peak.fetch_max(active, Ordering::SeqCst);
            tokio::task::yield_now().await;
            self.active.fetch_sub(1, Ordering::SeqCst);
            Batch::prepare(
                self.rows
                    .get(id)
                    .ok_or_else(|| Error::Unready("batch missing".into()))?,
            )
        }
    }
    fn limits() -> Limits {
        Limits {
            maximum_batches: 2,
            maximum_events: 2,
            maximum_bytes: 10000,
            concurrency: 2,
        }
    }
    fn fixture() -> (Memory, Certificate, Vec<Observation>) {
        let events: Vec<_> = (0..2)
            .map(|i| Observation {
                key: EventKey {
                    provider: 1,
                    instrument: 1,
                    session: 20260915,
                    kind: EventKind::Trade,
                    sequence: i + 1,
                },
                payload: Payload::Trade {
                    price: Decimal {
                        atoms: 100,
                        scale: 2,
                    },
                    size: Decimal { atoms: 1, scale: 0 },
                    exchange: 1,
                    trade_id: (i + 1).to_string(),
                    trf: None,
                    conditions: vec![],
                    correction: None,
                },
                sip: SourceTime {
                    ns: 10 + i,
                    precision_ns: 1,
                },
                participant: None,
                available_at_ns: 30 + i,
                receipt: None,
            })
            .collect();
        let mut rows = BTreeMap::new();
        let mut pages = vec![];
        for (i, event) in events.iter().enumerate() {
            let batch = Batch::prepare(std::slice::from_ref(event)).unwrap();
            let id = batch.id().unwrap();
            rows.insert(id.clone(), vec![event.clone()]);
            pages.push(Page {
                request_hash: if i == 0 { "c" } else { "d" }.repeat(64),
                response_hash: "e".repeat(64),
                next_request_hash: (i == 0).then(|| "d".repeat(64)),
                acquired_at_ns: 30 + i as u64,
                source_rows: 1,
                accepted_rows: 1,
                rejected_rows: 0,
                deduplicated_rows: 0,
                batches: vec![id],
                identity_checked: true,
                ordering_checked: true,
                interval_checked: true,
            });
        }
        (
            Memory {
                rows,
                calls: AtomicUsize::new(0),
                active: AtomicUsize::new(0),
                peak: AtomicUsize::new(0),
            },
            Certificate {
                schema_version: 1,
                authority: Authority {
                    provider: 1,
                    instrument: 1,
                    kind: EventKind::Trade,
                    source_revision: "r1".into(),
                    contract_hash: "a".repeat(64),
                    capabilities_hash: "b".repeat(64),
                },
                interval: Interval { start: 10, end: 20 },
                first_request_hash: "c".repeat(64),
                pages,
                published_at_ns: 40,
            },
            events,
        )
    }
    #[tokio::test]
    async fn concurrent_loading_preserves_certified_order_and_original_clocks() {
        let (reader, certificate, events) = fixture();
        let hash = certificate.id().unwrap();
        let source = load(&reader, certificate, &hash, 40, limits())
            .await
            .unwrap();
        assert_eq!(source.observations(), events);
        assert_eq!(source.certificate().id().unwrap(), hash);
        assert_eq!(reader.calls.load(Ordering::SeqCst), 2);
        assert_eq!(reader.peak.load(Ordering::SeqCst), 2);
        assert!(source
            .observations()
            .iter()
            .all(|e| e.receipt.is_none() && e.available_at_ns > e.sip.ns));
    }
    #[tokio::test]
    async fn certified_empty_interval_needs_no_fabricated_event_or_batch() {
        let (reader, mut certificate, _) = fixture();
        certificate.pages.truncate(1);
        let page = &mut certificate.pages[0];
        page.next_request_hash = None;
        page.source_rows = 0;
        page.accepted_rows = 0;
        page.batches.clear();
        let hash = certificate.id().unwrap();
        let source = load(&reader, certificate, &hash, 40, limits())
            .await
            .unwrap();
        assert!(source.observations().is_empty());
        assert!(source.certificate().empty());
        assert_eq!(reader.calls.load(Ordering::SeqCst), 0);
    }
    #[tokio::test]
    async fn missing_corrupt_future_and_over_budget_sources_never_return_partial_data() {
        for fault in 0..6 {
            let (mut reader, certificate, _) = fixture();
            let hash = certificate.id().unwrap();
            let mut budget = limits();
            let mut cutoff = 40;
            match fault {
                0 => cutoff = 39,
                1 => budget.maximum_batches = 1,
                2 => budget.maximum_events = 1,
                3 => budget.maximum_bytes = 1,
                4 => {
                    reader.rows.pop_first();
                }
                5 => {
                    reader.rows.values_mut().next().unwrap()[0].available_at_ns += 1;
                }
                _ => unreachable!(),
            }
            assert!(load(&reader, certificate, &hash, cutoff, budget)
                .await
                .is_err());
            if fault < 3 {
                assert_eq!(reader.calls.load(Ordering::SeqCst), 0);
            }
        }
    }
}
