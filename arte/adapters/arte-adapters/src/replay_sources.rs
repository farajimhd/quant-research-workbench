//! Certified REST source loading, before any historical clock projection.
//! Observations retain acquisition clocks and must not be passed off as live data.
use arte_core::{
    acquisition::{Certificate, VerifiedCertificate, Verifier},
    event_storage::Batch,
    events::{EventKind, Observation},
    strategy350_screen_join::RefinementPlan,
    Error, Result,
};
use futures_util::{stream, StreamExt, TryStreamExt};
use std::future::Future;
pub mod compact_bars;
pub mod projection;
pub mod startup;

#[derive(Clone, Copy)]
pub struct Limits {
    pub maximum_batches: usize,
    pub maximum_events: usize,
    pub maximum_bytes: usize,
    pub concurrency: usize,
}
impl Limits {
    fn validate(&self) -> Result<()> {
        if self.maximum_batches == 0
            || self.maximum_batches > 1_000_000
            || self.maximum_events == 0
            || self.maximum_events > 10_000_000
            || self.maximum_bytes == 0
            || self.maximum_bytes > 1024 * 1024 * 1024
            || self.concurrency == 0
            || self.concurrency > 64
        {
            return Err(Error::Capacity("replay source limits".into()));
        }
        Ok(())
    }
}
pub struct Source {
    certificate: VerifiedCertificate,
    observations: Vec<Observation>,
    trade_seconds: Option<arte_core::acquisition::trade_seconds::Index>,
}
/// Borrowed exact-refinement view. The full certified source remains intact
/// for market structure and V7; parameter sweeps reuse this ordered index.
pub struct RefinementView<'a> {
    source: &'a Source,
    indices: Vec<usize>,
    plan_hash: &'a str,
}
impl RefinementView<'_> {
    pub fn plan_hash(&self) -> &str {
        self.plan_hash
    }
    pub fn len(&self) -> usize {
        self.indices.len()
    }
    pub fn is_empty(&self) -> bool {
        self.indices.is_empty()
    }
    pub fn observations(&self) -> impl Iterator<Item = &Observation> {
        self.indices
            .iter()
            .map(|&index| &self.source.observations[index])
    }
}
impl Source {
    pub fn certificate(&self) -> &Certificate {
        self.certificate.certificate()
    }
    pub fn observations(&self) -> &[Observation] {
        &self.observations
    }
    pub fn refinement_view<'a>(
        &'a self,
        plan: &'a RefinementPlan,
        kind: EventKind,
        maximum_selected: usize,
    ) -> Result<RefinementView<'a>> {
        let certificate = self.certificate();
        if certificate.authority.provider != plan.scope().provider
            || certificate.authority.instrument != plan.scope().instrument
            || certificate.authority.kind != kind
            || certificate.interval != plan.source_interval()
        {
            return Err(Error::Conflict(
                "Strategy 350 refinement certificate authority".into(),
            ));
        }
        let indices = plan.selected_source_indices(&self.observations, kind, maximum_selected)?;
        Ok(RefinementView {
            source: self,
            indices,
            plan_hash: plan.evidence_hash(),
        })
    }
    pub fn prove_empty_trade_seconds(
        &self,
        interval: arte_core::coverage::Interval,
        as_of_ns: u64,
    ) -> Result<arte_core::acquisition::trade_seconds::EmptySpan> {
        self.trade_seconds
            .as_ref()
            .ok_or_else(|| {
                Error::Unready("source was not loaded with verified trade occupancy".into())
            })?
            .prove_empty(interval, as_of_ns)
    }
}
enum Verification {
    Plain(Verifier),
    Indexed(arte_core::acquisition::trade_seconds::Builder),
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
    load_inner(reader, certificate, expected_id, as_of_ns, limits, None).await
}
/// Explicit indexed loading. Alignment/budget failures do not fall back to plain
/// loading. Quotes cannot create trade-continuity authority.
pub async fn load_indexed_trades(
    reader: &impl Reader,
    certificate: Certificate,
    expected_id: &str,
    as_of_ns: u64,
    limits: Limits,
    scope: arte_core::event_order::Scope,
    maximum_seconds: usize,
) -> Result<Source> {
    load_inner(
        reader,
        certificate,
        expected_id,
        as_of_ns,
        limits,
        Some((scope, maximum_seconds)),
    )
    .await
}
async fn load_inner(
    reader: &impl Reader,
    certificate: Certificate,
    expected_id: &str,
    as_of_ns: u64,
    limits: Limits,
    index: Option<(arte_core::event_order::Scope, usize)>,
) -> Result<Source> {
    limits.validate()?;
    if certificate.id()? != expected_id || certificate.published_at_ns > as_of_ns {
        return Err(Error::Conflict(
            "replay source identity or knowledge cutoff".into(),
        ));
    }
    let mut verifier = match index {
        Some((scope, maximum)) => {
            Verification::Indexed(arte_core::acquisition::trade_seconds::Builder::new(
                certificate.clone(),
                scope,
                maximum,
            )?)
        }
        None => Verification::Plain(Verifier::new(certificate.clone())?),
    };
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
        match &mut verifier {
            Verification::Plain(v) => v.observe(&batch)?,
            Verification::Indexed(v) => v.observe(&batch)?,
        }
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
    let (certificate, trade_seconds) = match verifier {
        Verification::Plain(v) => (v.finish()?, None),
        Verification::Indexed(v) => {
            let (c, index) = v.finish()?;
            (c, Some(index))
        }
    };
    Ok(Source {
        certificate,
        observations,
        trade_seconds,
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
    struct StartupReader {
        batches: Memory,
        trade_id: String,
        quote_id: String,
        trade: Certificate,
        quote: Certificate,
        policy: arte_core::trade_eligibility::Policy,
        policy_hash: String,
    }
    #[tokio::test]
    async fn indexed_loading_verifies_empty_seconds_without_reading_batches_twice() {
        const S: u64 = 1_000_000_000;
        let (mut memory, mut certificate, mut events) = fixture();
        memory.rows.clear();
        certificate.interval.start *= S;
        certificate.interval.end *= S;
        certificate.published_at_ns *= S;
        for (i, event) in events.iter_mut().enumerate() {
            event.sip.ns *= S;
            event.available_at_ns *= S;
            let batch = Batch::prepare(std::slice::from_ref(event)).unwrap();
            let id = batch.id().unwrap();
            memory.rows.insert(id.clone(), vec![event.clone()]);
            certificate.pages[i].batches = vec![id];
            certificate.pages[i].acquired_at_ns = event.available_at_ns;
        }
        let id = certificate.id().unwrap();
        let scope = arte_core::event_order::Scope {
            provider: 1,
            instrument: 1,
            session: 20260915,
        };
        let source = load_indexed_trades(
            &memory,
            certificate.clone(),
            &id,
            40 * S,
            limits(),
            scope,
            10,
        )
        .await
        .unwrap();
        assert_eq!(memory.calls.load(Ordering::SeqCst), 2);
        assert_eq!(source.observations().len(), 2);
        source
            .prove_empty_trade_seconds(
                Interval {
                    start: 12 * S,
                    end: 20 * S,
                },
                40 * S,
            )
            .unwrap()
            .require(
                scope,
                Interval {
                    start: 12 * S,
                    end: 20 * S,
                },
                40 * S,
            )
            .unwrap();
        assert!(source
            .prove_empty_trade_seconds(
                Interval {
                    start: 10 * S,
                    end: 11 * S
                },
                40 * S
            )
            .is_err());
        assert!(source
            .prove_empty_trade_seconds(
                Interval {
                    start: 12 * S,
                    end: 20 * S
                },
                39 * S
            )
            .is_err());
        let plain = load(&memory, certificate.clone(), &id, 40 * S, limits())
            .await
            .unwrap();
        assert!(plain
            .prove_empty_trade_seconds(
                Interval {
                    start: 12 * S,
                    end: 20 * S
                },
                40 * S
            )
            .is_err());
        let before = memory.calls.load(Ordering::SeqCst);
        assert!(
            load_indexed_trades(&memory, certificate, &id, 40 * S, limits(), scope, 9)
                .await
                .is_err()
        );
        assert_eq!(memory.calls.load(Ordering::SeqCst), before);
    }
    impl Reader for StartupReader {
        async fn batch(&self, id: &str) -> Result<Batch> {
            self.batches.batch(id).await
        }
    }
    impl startup::Reader for StartupReader {
        async fn certificate(&self, id: &str) -> Result<Certificate> {
            if id == self.trade_id {
                Ok(self.trade.clone())
            } else if id == self.quote_id {
                Ok(self.quote.clone())
            } else {
                Err(Error::Unready("fixture certificate missing".into()))
            }
        }
        async fn policy(
            &self,
            _: u16,
            _: &str,
            _: u64,
        ) -> Result<arte_core::trade_eligibility::Pinned> {
            // Deliberately do not trust the adapter: the assembler must recheck.
            arte_core::trade_eligibility::Pinned::new(self.policy.clone(), &self.policy.hash()?)
        }
    }
    impl StartupReader {
        fn new() -> Self {
            let (batches, trade, _) = fixture();
            let mut quote = trade.clone();
            quote.authority.kind = EventKind::Quote;
            quote.pages.truncate(1);
            let page = &mut quote.pages[0];
            page.next_request_hash = None;
            page.source_rows = 0;
            page.accepted_rows = 0;
            page.batches.clear();
            let policy = arte_core::trade_eligibility::Policy {
                schema_version: 1,
                provider: 1,
                valid_from_ns: 10,
                valid_to_ns: 20,
                available_at_ns: 9,
                source_manifest_hash: "a".repeat(64),
                allowed_conditions: Default::default(),
                excluded_conditions: Default::default(),
                allow_empty_conditions: true,
            };
            Self {
                trade_id: trade.id().unwrap(),
                quote_id: quote.id().unwrap(),
                batches,
                trade,
                quote,
                policy_hash: policy.hash().unwrap(),
                policy,
            }
        }
        fn request(&self) -> startup::Request<'_> {
            startup::Request {
                scope: arte_core::event_order::Scope {
                    provider: 1,
                    instrument: 1,
                    session: 20260915,
                },
                interval: Interval { start: 10, end: 20 },
                trade_certificate: &self.trade_id,
                quote_certificate: &self.quote_id,
                trade_policy: &self.policy_hash,
                source_as_of_ns: 40,
                timing: projection::Policy { delay_ns: 2 },
            }
        }
    }
    fn startup_limits() -> startup::Limits {
        startup::Limits {
            channel: limits(),
            prepared: arte_core::market_structure::scheduler::playback::Limits {
                maximum_frames: 10,
                maximum_events: 2,
                maximum_serialized_bytes: 10000,
            },
        }
    }
    #[tokio::test]
    async fn startup_assembles_verified_channels_without_double_loading_batches() {
        let reader = StartupReader::new();
        let input = startup::load(&reader, reader.request(), startup_limits())
            .await
            .unwrap();
        assert_eq!(reader.batches.calls.load(Ordering::SeqCst), 2);
        assert_eq!(input.trades.observations().len(), 2);
        assert!(input.quotes.observations().is_empty());
        assert_eq!(input.trades.observations()[0].available_at_ns, 30);
        assert_eq!(input.projection.manifest.trade_certificate, reader.trade_id);
        assert_eq!(input.projection.manifest.quote_certificate, reader.quote_id);
        assert_eq!(
            input.projection.manifest.eligibility_policy,
            reader.policy_hash
        );
        input
            .projection
            .prepared
            .require_interval(Interval { start: 10, end: 20 })
            .unwrap();
    }
    #[tokio::test]
    async fn startup_fails_closed_before_batch_io_for_wrong_pins_and_cutoffs() {
        for fault in 0..6 {
            let mut reader = StartupReader::new();
            let mut limits = startup_limits();
            match fault {
                0 => reader.quote.interval.end = 21,
                1 => reader.policy.allow_empty_conditions = false,
                2 => {
                    reader.policy.available_at_ns = 11;
                    reader.policy_hash = reader.policy.hash().unwrap();
                }
                3 => limits.prepared.maximum_events = 1,
                4 => limits.channel.concurrency = 0,
                _ => {}
            }
            let mut request = reader.request();
            if fault == 5 {
                request.source_as_of_ns = 39;
            }
            assert!(
                startup::load(&reader, request, limits).await.is_err(),
                "fault {fault}"
            );
            assert_eq!(reader.batches.calls.load(Ordering::SeqCst), 0);
        }
    }
    #[tokio::test]
    async fn startup_loads_nonempty_quotes_and_preserves_both_channel_clocks() {
        let mut reader = StartupReader::new();
        let mut quote = fixture().2.remove(0);
        quote.key.kind = EventKind::Quote;
        let price = Decimal {
            atoms: 100,
            scale: 2,
        };
        let size = Decimal { atoms: 1, scale: 0 };
        quote.payload = Payload::Quote {
            bid: price,
            ask: price,
            bid_size: size,
            ask_size: size,
            bid_exchange: 1,
            ask_exchange: 1,
            conditions: vec![],
            indicators: vec![],
        };
        let batch = Batch::prepare(std::slice::from_ref(&quote)).unwrap();
        let id = batch.id().unwrap();
        reader.batches.rows.insert(id.clone(), vec![quote.clone()]);
        let page = &mut reader.quote.pages[0];
        page.source_rows = 1;
        page.accepted_rows = 1;
        page.batches = vec![id];
        reader.quote_id = reader.quote.id().unwrap();
        let mut limits = startup_limits();
        limits.prepared.maximum_events = 3;
        let input = startup::load(&reader, reader.request(), limits)
            .await
            .unwrap();
        assert_eq!(input.quotes.observations(), std::slice::from_ref(&quote));
        assert_eq!(reader.batches.calls.load(Ordering::SeqCst), 3);
        assert_eq!(reader.batches.peak.load(Ordering::SeqCst), 2);
        assert_eq!(input.projection.manifest.quote_certificate, reader.quote_id);
    }
    #[tokio::test]
    async fn startup_never_returns_partial_sources_after_missing_corrupt_or_over_budget_data() {
        for fault in 0..3 {
            let mut reader = StartupReader::new();
            let mut limits = startup_limits();
            match fault {
                0 => reader.batches.rows.clear(),
                1 => reader.batches.rows.values_mut().next().unwrap()[0].available_at_ns += 1,
                2 => limits.channel.maximum_bytes = 1,
                _ => unreachable!(),
            }
            assert!(startup::load(&reader, reader.request(), limits)
                .await
                .is_err());
            assert!(reader.batches.calls.load(Ordering::SeqCst) > 0);
        }
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
