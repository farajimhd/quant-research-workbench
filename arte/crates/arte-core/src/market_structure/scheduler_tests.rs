use super::*;
use crate::events::{Decimal, EventKind, Payload, SourceTime};
const SECOND: u64 = 1_000_000_000;
fn event(sequence: u64, second: u64, price: i64) -> Observation {
    Observation {
        key: EventKey {
            provider: 1,
            instrument: 1,
            session: 20260915,
            kind: EventKind::Trade,
            sequence,
        },
        payload: Payload::Trade {
            price: Decimal {
                atoms: price,
                scale: 0,
            },
            size: Decimal { atoms: 1, scale: 0 },
            exchange: 1,
            trade_id: sequence.to_string(),
            trf: None,
            conditions: vec![],
            correction: None,
        },
        sip: SourceTime {
            ns: second * SECOND + 1,
            precision_ns: 1,
        },
        participant: None,
        available_at_ns: second * SECOND + 2,
        receipt: None,
    }
}
fn scheduler(maximum_bars: usize) -> Scheduler {
    Scheduler::new(
        Ordered::new(super::super::tests::runtime(maximum_bars), 10).unwrap(),
        "causal-offline-test".into(),
    )
    .unwrap()
}
#[test]
fn every_boundary_is_seen_without_next_trade_or_empty_bar_leakage() {
    let mut scheduler = scheduler(10);
    for e in [event(3, 203, 30), event(1, 200, 10), event(2, 201, 20)] {
        scheduler.enqueue(&e, true).unwrap();
    }
    let mut seen = vec![];
    let mut ids = std::collections::BTreeSet::new();
    while scheduler.prepare_next(205 * SECOND, 205 * SECOND).unwrap() {
        let boundary = scheduler.pending().unwrap().unwrap();
        assert_eq!(boundary.evaluated_at_ns, 205 * SECOND);
        let input = boundary.input("test-feature-hash".into());
        assert_eq!(input.source_sequence, seen.len() as u64 + 1);
        assert_eq!(input.event_id, boundary.id);
        assert_eq!(input.evaluated_at_ns, 205 * SECOND);
        assert!(ids.insert(boundary.id.to_owned()));
        match boundary.kind {
            Kind::Completed(bar) => {
                assert_eq!(input.event_time_ns, bar.bar.end_ns);
                assert_eq!(input.available_at_ns, input.evaluated_at_ns);
                assert!(scheduler
                    .state()
                    .unwrap()
                    .market()
                    .unwrap()
                    .developing()
                    .is_none());
                seen.push(("bar", bar.bar.end_ns / SECOND));
                // The higher next trade has not entered the completed bar or VWAP.
                assert_eq!(
                    bar.bar.high,
                    match bar.bar.end_ns / SECOND {
                        201 => 10.,
                        202 => 20.,
                        204 => 30.,
                        _ => unreachable!(),
                    }
                );
                assert!(
                    scheduler
                        .state()
                        .unwrap()
                        .prior_strategy_levels()
                        .unwrap()
                        .0
                        < bar.bar.end_ns
                );
            }
            Kind::Trade {
                observation,
                eligible,
            } => {
                assert_eq!(input.event_time_ns, observation.sip.ns);
                assert_eq!(input.available_at_ns, observation.available_at_ns);
                assert!(eligible);
                assert_eq!(observation.available_at_ns, observation.sip.ns + 1);
                assert!(observation.receipt.is_none());
                seen.push(("trade", observation.key.sequence));
            }
        }
        let id = boundary.id.to_owned();
        scheduler.acknowledge(&id).unwrap();
    }
    assert_eq!(
        seen,
        vec![
            ("trade", 1),
            ("bar", 201),
            ("trade", 2),
            ("bar", 202),
            ("trade", 3),
            ("bar", 204)
        ]
    );
    assert_eq!(scheduler.pending_events(), 0);
    assert_eq!(
        scheduler
            .state()
            .unwrap()
            .market()
            .unwrap()
            .completed()
            .len(),
        3
    );
}
#[test]
fn awaiting_consumer_does_not_dequeue_or_recalculate_the_pending_boundary() {
    let mut scheduler = scheduler(10);
    scheduler.enqueue(&event(1, 200, 10), true).unwrap();
    scheduler.enqueue(&event(2, 201, 20), true).unwrap();
    scheduler.prepare_next(203 * SECOND, 203 * SECOND).unwrap();
    let id = scheduler.pending().unwrap().unwrap().id.to_owned();
    let hash = scheduler.state().unwrap().checkpoint().unwrap().hash;
    assert!(scheduler.prepare_next(204 * SECOND, 204 * SECOND).is_err());
    assert!(scheduler.acknowledge("another-boundary").is_err());
    assert_eq!(scheduler.pending().unwrap().unwrap().id, id);
    assert_eq!(scheduler.pending_events(), 2);
    assert_eq!(scheduler.state().unwrap().checkpoint().unwrap().hash, hash);
    scheduler.acknowledge(&id).unwrap();
    assert_eq!(scheduler.pending_events(), 1);
    assert!(scheduler.acknowledge(&id).is_err());
    scheduler.prepare_next(203 * SECOND, 203 * SECOND).unwrap();
    assert!(matches!(
        scheduler.pending().unwrap().unwrap().kind,
        Kind::Completed(_)
    ));
}
#[test]
fn calculation_failure_retains_unapplied_input_and_blocks_views() {
    let mut scheduler = scheduler(1);
    for e in [event(1, 200, 10), event(2, 201, 20), event(3, 202, 30)] {
        scheduler.enqueue(&e, true).unwrap();
    }
    for _ in 0..3 {
        scheduler.prepare_next(204 * SECOND, 204 * SECOND).unwrap();
        let id = scheduler.pending().unwrap().unwrap().id.to_owned();
        scheduler.acknowledge(&id).unwrap();
    }
    assert!(scheduler.prepare_next(204 * SECOND, 204 * SECOND).is_err());
    assert_eq!(scheduler.pending_events(), 1);
    assert!(scheduler.state().is_err());
    assert!(scheduler.pending().is_err());
}
