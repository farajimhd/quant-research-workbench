use super::*;
use arte_core::{
    acquisition::{Authority, Certificate, Page},
    coverage::Interval,
    event_order::Scope,
    event_storage::Batch,
    events::EventKind,
    market_structure::scheduler::playback,
    trade_eligibility,
};
use std::sync::atomic::{AtomicUsize, Ordering};
const S: u64 = 1_000_000_000;
struct Memory {
    seed: HistoricalSeed,
    trades: Certificate,
    quotes: Certificate,
    trade_id: String,
    quote_id: String,
    policy: trade_eligibility::Policy,
    policy_hash: String,
    seed_calls: AtomicUsize,
    certificate_calls: AtomicUsize,
}
impl crate::replay_sources::Reader for Memory {
    async fn batch(&self, _: &str) -> Result<Batch> {
        Err(Error::Conflict(
            "empty fixture unexpectedly requested event batch".into(),
        ))
    }
}
impl startup::Reader for Memory {
    async fn certificate(&self, id: &str) -> Result<Certificate> {
        self.certificate_calls.fetch_add(1, Ordering::SeqCst);
        if id == self.trade_id {
            Ok(self.trades.clone())
        } else if id == self.quote_id {
            Ok(self.quotes.clone())
        } else {
            Err(Error::Unready("fixture source missing".into()))
        }
    }
    async fn policy(&self, _: u16, _: &str, _: u64) -> Result<trade_eligibility::Pinned> {
        trade_eligibility::Pinned::new(self.policy.clone(), &self.policy_hash)
    }
}
impl Reader for Memory {
    async fn seed(&self, _: &str, _: u64, _: u32, _: u64) -> Result<HistoricalSeed> {
        self.seed_calls.fetch_add(1, Ordering::SeqCst);
        Ok(self.seed.clone())
    }
}
impl Memory {
    fn new(seed: &Bundle) -> Self {
        let trades = Certificate {
            schema_version: 1,
            authority: Authority {
                provider: 1,
                instrument: 1,
                kind: EventKind::Trade,
                source_revision: "fixture".into(),
                contract_hash: "a".repeat(64),
                capabilities_hash: "b".repeat(64),
            },
            interval: Interval {
                start: 200 * S,
                end: 300 * S,
            },
            first_request_hash: "c".repeat(64),
            published_at_ns: 400 * S,
            pages: vec![Page {
                request_hash: "c".repeat(64),
                response_hash: "d".repeat(64),
                next_request_hash: None,
                acquired_at_ns: 350 * S,
                source_rows: 0,
                accepted_rows: 0,
                rejected_rows: 0,
                deduplicated_rows: 0,
                batches: vec![],
                identity_checked: true,
                ordering_checked: true,
                interval_checked: true,
            }],
        };
        let mut quotes = trades.clone();
        quotes.authority.kind = EventKind::Quote;
        let policy = trade_eligibility::Policy {
            schema_version: 1,
            provider: 1,
            valid_from_ns: 200 * S,
            valid_to_ns: 300 * S,
            available_at_ns: 199 * S,
            source_manifest_hash: "a".repeat(64),
            allowed_conditions: Default::default(),
            excluded_conditions: Default::default(),
            allow_empty_conditions: true,
        };
        Self {
            seed: seed.hydrate().unwrap(),
            trade_id: trades.id().unwrap(),
            quote_id: quotes.id().unwrap(),
            trades,
            quotes,
            policy_hash: policy.hash().unwrap(),
            policy,
            seed_calls: AtomicUsize::new(0),
            certificate_calls: AtomicUsize::new(0),
        }
    }
    fn request(&self) -> startup::Request<'_> {
        startup::Request {
            scope: Scope {
                provider: 1,
                instrument: 1,
                session: 20260915,
            },
            interval: self.trades.interval,
            trade_certificate: &self.trade_id,
            quote_certificate: &self.quote_id,
            trade_policy: &self.policy_hash,
            source_as_of_ns: 400 * S,
            timing: crate::replay_sources::projection::Policy { delay_ns: 1 },
        }
    }
}
fn limits() -> startup::Limits {
    startup::Limits {
        channel: crate::replay_sources::Limits {
            maximum_batches: 1,
            maximum_events: 1,
            maximum_bytes: 10000,
            concurrency: 1,
        },
        prepared: playback::Limits {
            maximum_frames: 1,
            maximum_events: 1,
            maximum_serialized_bytes: 10000,
        },
    }
}
#[tokio::test]
async fn bootstrap_connects_loaded_sources_and_historical_seed_to_paused_market_run() {
    let (mut market, manifest, _, _, seed) = market::tests::fixture();
    let reader = Memory::new(&seed);
    // Planning computes identities before the run is frozen. Bootstrap must use
    // these pins unchanged; it never patches the manifest when data differs.
    let planned = startup::load(&reader, reader.request(), limits())
        .await
        .unwrap();
    let mut manifest = manifest.manifest().clone();
    manifest.source_manifest_hash = planned.projection.catalog.hash().unwrap();
    let manifest = Pinned::new(manifest.clone(), &manifest.hash().unwrap()).unwrap();
    market.manifest_hash = manifest.hash().into();
    let hash = market.hash().unwrap();
    let prepared = prepare(
        &reader,
        Request {
            manifest: &manifest,
            market,
            market_hash: &hash,
            source: reader.request(),
            limits: limits(),
            seed_id: &reader.seed.hash,
        },
    )
    .await
    .unwrap();
    assert_eq!(prepared.run.status().mode, playback::Mode::Paused);
    assert_eq!(prepared.run.status().admitted_events, 0);
    assert_eq!(prepared.seed.manifest.id, seed.manifest.id);
    assert_eq!(
        prepared.input.projection.prepared.hash(),
        planned.projection.prepared.hash()
    );
    assert_eq!(reader.seed_calls.load(Ordering::SeqCst), 1);
    let scope = reader.request().scope;
    let gap = Interval {
        start: 210 * S,
        end: 215 * S,
    };
    let proof = prepared
        .input
        .projection
        .empty_trade_span(&prepared.input.trades, &manifest, scope, gap, 400 * S)
        .unwrap();
    assert_eq!(proof.provenance().source_published_at_ns, 400 * S);
    assert_eq!(proof.provenance().modeled_available_at_ns, 215 * S + 1);
    proof.require(&manifest, scope, gap, 215 * S + 1).unwrap();
    assert!(proof.require(&manifest, scope, gap, 215 * S).is_err());
    assert!(proof
        .require(
            &manifest,
            Scope {
                instrument: 2,
                ..scope
            },
            gap,
            216 * S
        )
        .is_err());
    assert!(prepared
        .input
        .projection
        .empty_trade_span(&prepared.input.trades, &manifest, scope, gap, 399 * S)
        .is_err());
    assert!(prepared
        .input
        .projection
        .empty_trade_span(&planned.trades, &manifest, scope, gap, 400 * S)
        .is_err());
    let mut other = manifest.manifest().clone();
    other.source_manifest_hash = "f".repeat(64);
    let other = Pinned::new(other.clone(), &other.hash().unwrap()).unwrap();
    assert!(proof.require(&other, scope, gap, 216 * S).is_err());
    assert!(prepared
        .input
        .projection
        .empty_trade_span(&prepared.input.trades, &other, scope, gap, 400 * S)
        .is_err());
}

#[tokio::test]
async fn indexed_startup_rejects_bad_domains_before_source_reads() {
    let (_, _, _, _, seed) = market::tests::fixture();
    let reader = Memory::new(&seed);
    for fault in 0..3 {
        let mut request = reader.request();
        let maximum = match fault {
            0 => 0,
            1 => 99,
            _ => {
                request.interval.start += 1;
                100
            }
        };
        assert!(startup::load_indexed(&reader, request, limits(), maximum)
            .await
            .is_err());
    }
    assert_eq!(reader.certificate_calls.load(Ordering::SeqCst), 0);
}
#[tokio::test]
async fn bootstrap_rejects_changed_domain_and_seed_before_loading_sources() {
    for fault in 0..4 {
        let (mut market, manifest, _, _, seed) = market::tests::fixture();
        let mut reader = Memory::new(&seed);
        let mut hash = market.hash().unwrap();
        let seed_id = reader.seed.hash.clone();
        match fault {
            0 => hash = "0".repeat(64),
            1 => {
                market.configuration.provider = 2;
                hash = market.hash().unwrap();
            }
            2 => reader.seed.hash = "0".repeat(64),
            3 => reader.seed.levels.clear(),
            _ => unreachable!(),
        }
        // Ensure a deterministic seed integrity failure even if fixture has no levels.
        if fault == 3 {
            reader.seed.configuration_hash.push('x');
        }
        assert!(prepare(
            &reader,
            Request {
                manifest: &manifest,
                market,
                market_hash: &hash,
                source: reader.request(),
                limits: limits(),
                seed_id: &seed_id
            }
        )
        .await
        .is_err());
        assert_eq!(reader.certificate_calls.load(Ordering::SeqCst), 0);
        assert_eq!(
            reader.seed_calls.load(Ordering::SeqCst),
            usize::from(fault >= 2)
        );
    }
}
