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
            Kind::Completed {
                interval_ns, bar, ..
            } => {
                assert_eq!(interval_ns, SECOND);
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
        Kind::Completed { .. }
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

#[test]
fn timeframes_close_in_order_without_leaking_later_macd_or_filling_empty_intervals() {
    let runtime = super::super::tests::runtime_with_timeframes(
        100,
        vec![super::super::Timeframe {
            interval_ns: 5 * SECOND,
            macd_periods: (12, 26, 9),
            maximum_bars: 20,
        }],
    );
    let mut scheduler =
        Scheduler::new(Ordered::new(runtime, 10).unwrap(), "multi-timeframe".into()).unwrap();
    for e in [event(1, 200, 10), event(2, 204, 20), event(3, 211, 30)] {
        scheduler.enqueue(&e, true).unwrap();
    }
    let mut closes = vec![];
    let mut macd = crate::strategy_macd::State::new(true);
    let mut evaluated_at_ns = 220 * SECOND;
    while scheduler
        .prepare_next(220 * SECOND, evaluated_at_ns)
        .unwrap()
    {
        let boundary = scheduler.pending().unwrap().unwrap();
        let macd_reading = macd
            .observe_boundary(&boundary, scheduler.state().unwrap())
            .unwrap();
        assert!(macd
            .observe_boundary(&boundary, scheduler.state().unwrap())
            .unwrap()
            .is_none());
        if let Kind::Completed {
            interval_ns,
            bar,
            available_at_ns,
        } = &boundary.kind
        {
            closes.push((*interval_ns / SECOND, bar.bar.end_ns / SECOND));
            if bar.bar.end_ns == 201 * SECOND {
                assert_eq!(
                    macd_reading.as_ref().unwrap().kind,
                    crate::strategy_macd::Kind::Unavailable
                );
                let five = scheduler.state().unwrap().timeframe(5 * SECOND).unwrap();
                assert!(five.completed().is_empty());
                assert_eq!(five.developing().unwrap().close, 10.);
            }
            if bar.bar.end_ns == 205 * SECOND {
                assert_eq!(bar.bar.close, 20.);
                let five = scheduler.state().unwrap().timeframe(5 * SECOND).unwrap();
                assert_eq!(five.completed().len(), 1);
                assert!(five.developing().is_none());
                assert_eq!(*available_at_ns, 220 * SECOND);
                if *interval_ns == SECOND {
                    assert_eq!(
                        macd_reading.as_ref().unwrap().kind,
                        crate::strategy_macd::Kind::Completed
                    );
                    let mut skipped = crate::strategy_macd::State::new(true);
                    assert!(skipped
                        .observe_boundary(&boundary, scheduler.state().unwrap())
                        .is_err());
                    assert!(skipped.reading().is_none());
                    assert_eq!(boundary.evaluated_at_ns, 221 * SECOND);
                    assert_eq!(
                        boundary.input("features".into()).available_at_ns,
                        220 * SECOND
                    );
                } else {
                    evaluated_at_ns += SECOND;
                }
            }
        }
        let id = boundary.id.to_owned();
        scheduler.acknowledge(&id).unwrap();
    }
    assert_eq!(
        closes,
        vec![(1, 201), (5, 205), (1, 205), (1, 212), (5, 215)]
    );
    let five = scheduler.state().unwrap().timeframe(5 * SECOND).unwrap();
    assert_eq!(five.completed().len(), 2);
    assert_eq!(five.completed()[0].bar.volume, 2.);
    assert_eq!(five.completed()[1].bar.volume, 1.);
    assert!(five.completed()[1].macd.0 > five.completed()[1].macd.1);
}
