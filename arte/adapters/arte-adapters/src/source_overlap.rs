//! Diagnostic REST/WebSocket source overlap comparison. Equality is evidence
//! about the captured overlap only, never live subscription or feed completeness.
use arte_core::{
    acquisition::VerifiedCertificate,
    content_hash,
    coverage::Interval,
    event_order::Scope,
    event_storage::Batch,
    events::{EventKey, Observation},
    Error, Result,
};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

const MILLISECOND: u64 = 1_000_000;
#[derive(Clone, Copy)]
pub struct Limits {
    pub maximum_batches: usize,
    pub maximum_events: usize,
}
#[derive(Debug, Clone)]
pub struct Comparison {
    pub certificate_id: String,
    pub overlap: Interval,
    pub events: usize,
    pub fingerprint: String,
}
#[derive(Serialize, PartialEq, Eq)]
struct SourceEvent {
    sip_millisecond: u64,
    payload_hash: String,
}
struct Input<'a> {
    scope: Scope,
    kind: arte_core::events::EventKind,
    overlap: Interval,
    maximum: usize,
    as_of_ns: u64,
    run_id: &'a str,
}
fn insert(
    rows: &mut BTreeMap<EventKey, SourceEvent>,
    event: &Observation,
    input: &Input<'_>,
    live: bool,
) -> Result<()> {
    event.validate()?;
    if event.key.provider != input.scope.provider
        || event.key.instrument != input.scope.instrument
        || event.key.session != input.scope.session
        || event.key.kind != input.kind
        || event.available_at_ns > input.as_of_ns
        || (live
            && (event
                .receipt
                .as_ref()
                .is_none_or(|r| r.run_id != input.run_id)
                || event.sip.precision_ns != MILLISECOND as u32
                || !event.sip.ns.is_multiple_of(MILLISECOND)))
        || (!live && (event.receipt.is_some() || event.sip.precision_ns > MILLISECOND as u32))
    {
        return Err(Error::Conflict(
            "source overlap scope or clock differs".into(),
        ));
    }
    if event.sip.ns < input.overlap.start || event.sip.ns >= input.overlap.end {
        return Ok(());
    }
    if rows.len() >= input.maximum {
        return Err(Error::Capacity("source overlap event budget".into()));
    }
    if rows
        .insert(
            event.key.clone(),
            SourceEvent {
                sip_millisecond: event.sip.ns / MILLISECOND,
                payload_hash: content_hash(&event.payload)?,
            },
        )
        .is_some()
    {
        return Err(Error::Conflict("duplicate overlap source identity".into()));
    }
    Ok(())
}
/// Compare an exact persisted REST batch chain with captured live observations.
/// Massive WebSocket SIP time is millisecond precision while REST can be finer;
/// provider sequence and normalized payload must still match exactly.
#[allow(clippy::too_many_arguments)]
pub fn compare_massive(
    verified: &VerifiedCertificate,
    batches: &[Batch],
    stream: &[Observation],
    scope: Scope,
    overlap: Interval,
    run_id: &str,
    as_of_ns: u64,
    limits: Limits,
) -> Result<Comparison> {
    overlap.validate()?;
    let cert = verified.certificate();
    let expected_count: usize = cert.pages.iter().map(|page| page.batches.len()).sum();
    if scope.provider != 1
        || run_id.is_empty()
        || limits.maximum_batches == 0
        || limits.maximum_events == 0
        || limits.maximum_events > 2_000_000
        || batches.len() != expected_count
        || batches.len() > limits.maximum_batches
        || stream.len() > limits.maximum_events
        || cert.authority.provider != scope.provider
        || cert.authority.instrument != scope.instrument
        || cert.published_at_ns > as_of_ns
        || overlap.start < cert.interval.start
        || overlap.end > cert.interval.end
        || !overlap.start.is_multiple_of(MILLISECOND)
        || !overlap.end.is_multiple_of(MILLISECOND)
    {
        return Err(Error::Unready("source overlap authority or bounds".into()));
    }
    let input = Input {
        scope,
        kind: cert.authority.kind,
        overlap,
        maximum: limits.maximum_events,
        as_of_ns,
        run_id,
    };
    let mut rest = BTreeMap::new();
    for (batch, expected_id) in batches
        .iter()
        .zip(cert.pages.iter().flat_map(|page| &page.batches))
    {
        if batch.id()? != *expected_id {
            return Err(Error::Conflict("source overlap REST batch identity".into()));
        }
        batch.verify_readback(batch.payloads(), batch.observations())?;
        for event in batch.hydrate()? {
            insert(&mut rest, &event, &input, false)?;
        }
    }
    let mut live = BTreeMap::new();
    for event in stream {
        insert(&mut live, event, &input, true)?;
    }
    if rest.is_empty() || rest != live {
        return Err(Error::Conflict(
            "REST and WebSocket overlap identities or payloads differ".into(),
        ));
    }
    let certificate_id = cert.id()?;
    let mut digest = Sha256::new();
    let header = serde_json::to_vec(&(
        "arte.massive-source-overlap.v1",
        &certificate_id,
        scope.provider,
        scope.instrument,
        scope.session,
        overlap,
        run_id,
    ))
    .map_err(|e| Error::Serialization(e.to_string()))?;
    digest.update((header.len() as u64).to_be_bytes());
    digest.update(header);
    for (key, event) in &rest {
        let row =
            serde_json::to_vec(&(key, event)).map_err(|e| Error::Serialization(e.to_string()))?;
        digest.update((row.len() as u64).to_be_bytes());
        digest.update(row);
    }
    let fingerprint = format!("{:x}", digest.finalize());
    Ok(Comparison {
        certificate_id,
        overlap,
        events: rest.len(),
        fingerprint,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::{
        acquisition::{Authority, Certificate, Page, Verifier},
        events::{Decimal, EventKind, Payload, Receipt, SourceTime},
    };
    const S: u64 = 1_000_000_000;
    fn scope() -> Scope {
        Scope {
            provider: 1,
            instrument: 10,
            session: 20260922,
        }
    }
    fn rest() -> Observation {
        Observation {
            key: EventKey {
                provider: 1,
                instrument: 10,
                session: 20260922,
                kind: EventKind::Trade,
                sequence: 7,
            },
            payload: Payload::Trade {
                price: Decimal::parse("10.25").unwrap(),
                size: Decimal::parse("1").unwrap(),
                exchange: 1,
                trade_id: "trade-7".into(),
                trf: None,
                conditions: vec![],
                correction: None,
            },
            sip: SourceTime {
                ns: 2 * S + 123_456_789,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: 5 * S,
            receipt: None,
        }
    }
    fn fixture() -> (VerifiedCertificate, Vec<Batch>, Observation) {
        let event = rest();
        let batch = Batch::prepare(std::slice::from_ref(&event)).unwrap();
        let certificate = Certificate {
            schema_version: 1,
            authority: Authority {
                provider: 1,
                instrument: 10,
                kind: EventKind::Trade,
                source_revision: "test".into(),
                contract_hash: "c".repeat(64),
                capabilities_hash: "d".repeat(64),
            },
            interval: Interval {
                start: S,
                end: 5 * S,
            },
            first_request_hash: "a".repeat(64),
            pages: vec![Page {
                request_hash: "a".repeat(64),
                response_hash: "b".repeat(64),
                next_request_hash: None,
                acquired_at_ns: 5 * S,
                source_rows: 1,
                accepted_rows: 1,
                rejected_rows: 0,
                deduplicated_rows: 0,
                batches: vec![batch.id().unwrap()],
                identity_checked: true,
                ordering_checked: true,
                interval_checked: true,
            }],
            published_at_ns: 6 * S,
        };
        let mut verifier = Verifier::new(certificate).unwrap();
        verifier.observe(&batch).unwrap();
        let mut live = event;
        live.sip = SourceTime {
            ns: 2 * S + 123_000_000,
            precision_ns: MILLISECOND as u32,
        };
        live.available_at_ns = 4 * S;
        live.receipt = Some(Receipt {
            run_id: "live-run".into(),
            lane: 1,
            sequence: 1,
            utc_ns: 4 * S,
            monotonic_ns: 100,
        });
        (verifier.finish().unwrap(), vec![batch], live)
    }
    fn overlap() -> Interval {
        Interval {
            start: 2 * S,
            end: 3 * S,
        }
    }
    fn limits() -> Limits {
        Limits {
            maximum_batches: 2,
            maximum_events: 2,
        }
    }
    #[test]
    fn exact_overlap_matches_at_websocket_precision_but_grants_no_readiness() {
        let (verified, batches, live) = fixture();
        let report = compare_massive(
            &verified,
            &batches,
            std::slice::from_ref(&live),
            scope(),
            overlap(),
            "live-run",
            7 * S,
            limits(),
        )
        .unwrap();
        assert_eq!(report.events, 1);
        assert_eq!(report.overlap, overlap());
        assert_eq!(report.certificate_id, verified.certificate().id().unwrap());
        assert_eq!(report.fingerprint.len(), 64);
        let mut changed = live.clone();
        if let Payload::Trade { price, .. } = &mut changed.payload {
            *price = Decimal::parse("10.26").unwrap();
        }
        let mut wrong_sequence = live.clone();
        wrong_sequence.key.sequence += 1;
        let mut wrong_bucket = live.clone();
        wrong_bucket.sip.ns += MILLISECOND;
        for stream in [
            vec![],
            vec![changed],
            vec![wrong_sequence],
            vec![wrong_bucket],
            vec![live.clone(), live.clone()],
        ] {
            assert!(compare_massive(
                &verified,
                &batches,
                &stream,
                scope(),
                overlap(),
                "live-run",
                7 * S,
                limits(),
            )
            .is_err());
        }
        assert!(compare_massive(
            &verified,
            &batches,
            std::slice::from_ref(&live),
            scope(),
            overlap(),
            "other-run",
            7 * S,
            limits(),
        )
        .is_err());
        assert!(compare_massive(
            &verified,
            &[],
            std::slice::from_ref(&live),
            scope(),
            overlap(),
            "live-run",
            7 * S,
            limits(),
        )
        .is_err());
    }
}
