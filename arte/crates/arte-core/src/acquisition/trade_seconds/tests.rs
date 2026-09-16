use super::*;
use crate::{
    acquisition::{Authority, Page},
    events::*,
};
fn scope() -> Scope {
    Scope {
        provider: 1,
        instrument: 1,
        session: 20260915,
    }
}
fn interval(start: u64, end: u64) -> Interval {
    Interval {
        start: start * SECOND,
        end: end * SECOND,
    }
}
fn batch(session: u32) -> Batch {
    Batch::prepare(&[Observation {
        key: EventKey {
            provider: 1,
            instrument: 1,
            session,
            kind: EventKind::Trade,
            sequence: 1,
        },
        payload: Payload::Trade {
            price: Decimal::parse("10").unwrap(),
            size: Decimal::parse("1").unwrap(),
            exchange: 1,
            trade_id: "trade".into(),
            trf: None,
            conditions: vec![99],
            correction: None,
        },
        sip: SourceTime {
            ns: 15 * SECOND,
            precision_ns: 1,
        },
        participant: None,
        available_at_ns: 30 * SECOND,
        receipt: None,
    }])
    .unwrap()
}
fn certificate(batch: Option<&Batch>) -> Certificate {
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
        interval: interval(10, 20),
        first_request_hash: "c".repeat(64),
        pages: vec![Page {
            request_hash: "c".repeat(64),
            response_hash: "d".repeat(64),
            next_request_hash: None,
            acquired_at_ns: 30 * SECOND,
            source_rows: u64::from(batch.is_some()),
            accepted_rows: u64::from(batch.is_some()),
            rejected_rows: 0,
            deduplicated_rows: 0,
            batches: batch.map(|b| vec![b.id().unwrap()]).unwrap_or_default(),
            identity_checked: true,
            ordering_checked: true,
            interval_checked: true,
        }],
        published_at_ns: 40 * SECOND,
    }
}
#[test]
fn only_readback_verified_unoccupied_seconds_can_be_proven_empty() {
    let batch = batch(20260915);
    let cert = certificate(Some(&batch));
    assert!(Builder::new(cert.clone(), scope(), 10)
        .unwrap()
        .finish()
        .is_err());
    let mut builder = Builder::new(cert.clone(), scope(), 10).unwrap();
    builder.observe(&batch).unwrap();
    let (verified, index) = builder.finish().unwrap();
    assert_eq!(verified.certificate().id().unwrap(), cert.id().unwrap());
    assert!(index.prove_empty(interval(10, 15), 39 * SECOND).is_err());
    let proof = index.prove_empty(interval(10, 15), 40 * SECOND).unwrap();
    assert_eq!(proof.certificate_id(), cert.id().unwrap());
    assert_eq!(proof.published_at_ns(), 40 * SECOND);
    assert_eq!(
        proof.fingerprint(),
        index
            .prove_empty(interval(10, 15), 50 * SECOND)
            .unwrap()
            .fingerprint()
    );
    proof
        .require(scope(), interval(10, 15), 40 * SECOND)
        .unwrap();
    assert!(proof
        .require(scope(), interval(10, 14), 40 * SECOND)
        .is_err());
    assert!(proof
        .require(
            Scope {
                session: 20260916,
                ..scope()
            },
            interval(10, 15),
            40 * SECOND
        )
        .is_err());
    assert!(proof
        .require(scope(), interval(10, 15), 39 * SECOND)
        .is_err());
    assert!(index.prove_empty(interval(15, 16), 40 * SECOND).is_err());
    assert!(index.prove_empty(interval(10, 20), 40 * SECOND).is_err());
    assert!(index.prove_empty(interval(16, 20), 40 * SECOND).is_ok());
    assert!(index.prove_empty(interval(9, 15), 40 * SECOND).is_err());
    assert!(index
        .prove_empty(
            Interval {
                start: 10 * SECOND + 1,
                end: 15 * SECOND
            },
            40 * SECOND
        )
        .is_err());
}
#[test]
fn empty_pages_bounds_and_wrong_session_fail_closed() {
    let cert = certificate(None);
    let (_, index) = Builder::new(cert.clone(), scope(), 10)
        .unwrap()
        .finish()
        .unwrap();
    index.prove_empty(interval(10, 20), 40 * SECOND).unwrap();
    for budget in [0, 9, 172801] {
        assert!(Builder::new(cert.clone(), scope(), budget).is_err());
    }
    let mut bad = cert.clone();
    bad.interval.end += 1;
    assert!(Builder::new(bad, scope(), 11).is_err());
    let mut bad = cert.clone();
    bad.pages[0].interval_checked = false;
    assert!(Builder::new(bad, scope(), 10).is_err());
    let wrong = batch(20260916);
    let mut builder = Builder::new(certificate(Some(&wrong)), scope(), 10).unwrap();
    assert!(builder.observe(&wrong).is_err());
    assert!(builder.finish().is_err());
}
