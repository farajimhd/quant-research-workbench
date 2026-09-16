use super::*;
use crate::events::{Decimal, EventKey, EventKind, Observation, SourceTime};

fn market() -> market_structure::Runtime {
    market_structure::tests::runtime_with_timeframes(20, vec![])
}
fn config() -> Config {
    Config {
        tick: 0.01,
        settings: Settings {
            breakout_buffer_ticks: 1.,
            breakout_buffer_bps: 0.,
            rejection_break_offset_bps: 10.,
            topping_tail_fraction: 0.5,
            maximum_encounters: 10,
        },
        maximum_prior_levels: 100,
    }
}
fn event(sequence: u64, second: u64) -> Observation {
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
                atoms: 97,
                scale: 1,
            },
            size: Decimal { atoms: 1, scale: 0 },
            exchange: 1,
            trade_id: sequence.to_string(),
            trf: None,
            conditions: vec![],
            correction: None,
        },
        sip: SourceTime {
            ns: second * SECOND + sequence,
            precision_ns: 1,
        },
        participant: None,
        available_at_ns: second * SECOND + sequence + 1,
        receipt: None,
    }
}
fn boundary(event: &Observation, sequence: u64, eligible: bool) -> Boundary<'_> {
    Boundary {
        id: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        sequence,
        evaluated_at_ns: event.available_at_ns,
        kind: Kind::Trade {
            observation: event,
            eligible,
        },
    }
}
fn warning(runtime: &mut Runtime) {
    use crate::{market::Bar, strategy_encounters::Level, v7_encounters::ActiveRole};
    let bar = |start, open, high, low, close| Bar {
        start_ns: start * SECOND,
        end_ns: (start + 1) * SECOND,
        open,
        high,
        low,
        close,
        volume: 1.,
        notional: close,
        trades: 1,
    };
    runtime
        .state
        .completed_bar(
            20260915,
            &bar(199, 10., 10.2, 9.8, 9.9),
            Some(&bar(198, 9.8, 9.9, 9.7, 9.8)),
            &[Level {
                id: "resistance".into(),
                price: 10.,
                lower: 9.9,
                upper: 10.1,
                role: ActiveRole::Resistance,
                confirmed_at_ns: SECOND,
            }],
            &runtime.config.settings,
            runtime.config.tick,
        )
        .unwrap();
    assert!(runtime.state.blocked());
}

#[test]
fn only_eligible_trades_consume_opening_evidence_and_retries_are_exact() {
    let market = market();
    let mut runtime = Runtime::new(&market, config()).unwrap();
    warning(&mut runtime);
    let mut quote = event(1, 200);
    quote.key.kind = EventKind::Quote;
    quote.payload = Payload::Quote {
        bid: Decimal {
            atoms: 97,
            scale: 1,
        },
        ask: Decimal {
            atoms: 98,
            scale: 1,
        },
        bid_size: Decimal { atoms: 1, scale: 0 },
        ask_size: Decimal { atoms: 1, scale: 0 },
        bid_exchange: 1,
        ask_exchange: 1,
        conditions: vec![],
        indicators: vec![],
    };
    let mut first = boundary(&quote, 1, false);
    first.kind = Kind::Quote {
        observation: &quote,
    };
    assert!(runtime.observe(&first, &market).unwrap());
    let excluded = event(2, 200);
    assert!(runtime
        .observe(&boundary(&excluded, 2, false), &market)
        .unwrap());
    assert!(
        !runtime.state().unwrap().levels["resistance"]
            .warning
            .as_ref()
            .unwrap()
            .opening_seen
    );
    let trade = event(3, 200);
    let next = boundary(&trade, 3, true);
    assert!(runtime.observe(&next, &market).unwrap());
    assert_eq!(
        runtime.snapshot().unwrap().unwrap().exit_reason,
        Some(ExitReason::ToppingRejectionNextOpen)
    );
    let before = content_hash(runtime.state().unwrap()).unwrap();
    assert!(!runtime.observe(&next, &market).unwrap());
    assert_eq!(content_hash(runtime.state().unwrap()).unwrap(), before);
    let following = event(4, 200);
    runtime
        .observe(&boundary(&following, 4, true), &market)
        .unwrap();
    assert!(runtime.snapshot().unwrap().unwrap().exit_reason.is_none());
}

#[test]
fn rejected_boundaries_poison_owner_instead_of_skipping() {
    let market = market();
    for case in 0..5 {
        let mut runtime = Runtime::new(&market, config()).unwrap();
        let mut event = event(1, 200);
        if case == 2 {
            event.key.instrument = 2;
        }
        let mut input = boundary(&event, 1, true);
        match case {
            0 => input.sequence = 2,
            1 => input.evaluated_at_ns = event.sip.ns - 1,
            2 => (),
            3 => {
                input.kind = Kind::Quote {
                    observation: &event,
                }
            }
            4 => {
                runtime.observe(&input, &market).unwrap();
                input.kind = Kind::Trade {
                    observation: &event,
                    eligible: false,
                };
            }
            _ => unreachable!(),
        }
        assert!(runtime.observe(&input, &market).is_err(), "case {case}");
        assert!(runtime.snapshot().is_err());
        assert!(runtime.state().is_err());
    }
}

#[test]
fn real_scheduler_consumes_every_boundary_once() {
    use market_structure::{scheduler::Scheduler, Ordered};
    let mut scheduler =
        Scheduler::new(Ordered::new(market(), 10).unwrap(), "encounter-test".into()).unwrap();
    let mut runtime = Runtime::new(scheduler.state().unwrap(), config()).unwrap();
    for (seq, sec) in [(1, 200), (2, 201), (3, 203)] {
        scheduler.enqueue(&event(seq, sec), true).unwrap();
    }
    let mut count = 0;
    while scheduler.prepare_next(205 * SECOND, 205 * SECOND).unwrap() {
        let boundary = scheduler.pending().unwrap().unwrap();
        assert!(runtime
            .observe(&boundary, scheduler.state().unwrap())
            .unwrap());
        assert!(!runtime
            .observe(&boundary, scheduler.state().unwrap())
            .unwrap());
        let context = "c".repeat(64);
        let image = runtime
            .checkpoint(&context, scheduler.state().unwrap(), &boundary, 100_000)
            .unwrap();
        let mut restored = Runtime::restore_checkpoint(
            &image,
            &image.id,
            &context,
            scheduler.state().unwrap(),
            config(),
            &boundary,
            100_000,
        )
        .unwrap();
        assert!(!restored
            .observe(&boundary, scheduler.state().unwrap())
            .unwrap());
        assert_eq!(
            restored
                .checkpoint(&context, scheduler.state().unwrap(), &boundary, 100_000)
                .unwrap()
                .payload,
            image.payload
        );
        runtime = restored;
        count += 1;
        assert_eq!(runtime.snapshot().unwrap().unwrap().sequence, count);
        let id = boundary.id.to_owned();
        scheduler.acknowledge(&id).unwrap();
    }
    assert_eq!(count, 6);
}

#[test]
fn warning_recovery_preserves_next_open_and_failed_state() {
    let market = market();
    let mut runtime = Runtime::new(&market, config()).unwrap();
    warning(&mut runtime);
    let excluded = event(1, 200);
    let first = boundary(&excluded, 1, false);
    runtime.observe(&first, &market).unwrap();
    let context = "c".repeat(64);
    let image = runtime
        .checkpoint(&context, &market, &first, 100_000)
        .unwrap();
    let mut restored = Runtime::restore_checkpoint(
        &image,
        &image.id,
        &context,
        &market,
        config(),
        &first,
        100_000,
    )
    .unwrap();
    let trade = event(2, 200);
    let next = boundary(&trade, 2, true);
    runtime.observe(&next, &market).unwrap();
    restored.observe(&next, &market).unwrap();
    assert_eq!(
        restored.snapshot().unwrap().unwrap().exit_reason,
        Some(ExitReason::ToppingRejectionNextOpen)
    );
    let expected = runtime
        .checkpoint(&context, &market, &next, 100_000)
        .unwrap();
    assert_eq!(
        restored
            .checkpoint(&context, &market, &next, 100_000)
            .unwrap()
            .payload,
        expected.payload
    );
    let failed = Runtime::restore_checkpoint(
        &expected,
        &expected.id,
        &context,
        &market,
        config(),
        &next,
        100_000,
    )
    .unwrap();
    assert!(failed.state().unwrap().blocked());
}

#[test]
fn recovery_rejects_corruption_wrong_pins_and_semantic_tampering() {
    use crate::seed_storage::Object;
    let market = market();
    let mut runtime = Runtime::new(&market, config()).unwrap();
    let event = event(1, 200);
    let input = boundary(&event, 1, true);
    let context = "c".repeat(64);
    assert!(runtime
        .checkpoint(&context, &market, &input, 100_000)
        .is_err());
    runtime.observe(&input, &market).unwrap();
    let image = runtime
        .checkpoint(&context, &market, &input, 100_000)
        .unwrap();
    assert!(runtime.checkpoint(&context, &market, &input, 1).is_err());
    for case in 0..8 {
        let mut value: serde_json::Value = serde_json::from_slice(&image.payload).unwrap();
        match case {
            0 => value["version"] = 2.into(),
            1 => value["context"] = "d".repeat(64).into(),
            2 => value["configuration"] = "d".repeat(64).into(),
            3 => value["market"] = "d".repeat(64).into(),
            4 => value["snapshot"]["sequence"] = 2.into(),
            5 => value["snapshot"]["blocked"] = true.into(),
            6 => value["state"]["session"] = 1.into(),
            7 => value["state"]["at_ns"] = (300 * SECOND).into(),
            _ => unreachable!(),
        }
        let changed = Object::new(serde_json::to_vec(&value).unwrap());
        assert!(
            Runtime::restore_checkpoint(
                &changed,
                &changed.id,
                &context,
                &market,
                config(),
                &input,
                100_000
            )
            .is_err(),
            "case {case}"
        );
    }
    assert!(Runtime::restore_checkpoint(
        &image,
        &"d".repeat(64),
        &context,
        &market,
        config(),
        &input,
        100_000
    )
    .is_err());
    assert!(
        Runtime::restore_checkpoint(&image, &image.id, &context, &market, config(), &input, 1)
            .is_err()
    );
    let mut changed_config = config();
    changed_config.tick = 0.02;
    assert!(Runtime::restore_checkpoint(
        &image,
        &image.id,
        &context,
        &market,
        changed_config,
        &input,
        100_000
    )
    .is_err());
}

#[test]
fn restrictions_never_grant_permission_and_require_exact_boundary() {
    let market = market();
    let mut runtime = Runtime::new(&market, config()).unwrap();
    warning(&mut runtime);
    let event = event(1, 200);
    let mut input = boundary(&event, 1, true);
    runtime.observe(&input, &market).unwrap();
    let mut admission = crate::candidate_features::AdmissionAuthorities {
        at_ns: 200 * SECOND,
        permissions: false,
        session_open: false,
        tradable: false,
        encounter_blocked: false,
        regular_block: Some("external".into()),
    };
    let mut safety = crate::strategy_dispatch::Safety {
        position_quantity: 1,
        pending_exit_quantity: 0,
        exit_pending: false,
        pending_entry: false,
        last_exit_reason: None,
        flatten: false,
        protective_stop_crossed: false,
        manual_exit: true,
        completed_macd_reversal: false,
        setup_phase: crate::strategy_lifecycle::Phase::Building,
        luld_buffer_reached: false,
        encounter_exit: false,
        early_setup_failed: false,
        structural_exit: false,
    };
    runtime
        .restrict(&input, &mut admission, &mut safety)
        .unwrap();
    assert!(admission.encounter_blocked && safety.encounter_exit && safety.manual_exit);
    assert!(!admission.permissions && !admission.session_open && !admission.tradable);
    assert_eq!(admission.regular_block.as_deref(), Some("external"));
    input.sequence += 1;
    admission.encounter_blocked = false;
    safety.encounter_exit = false;
    assert!(runtime
        .restrict(&input, &mut admission, &mut safety)
        .is_err());
    assert!(!admission.encounter_blocked && !safety.encounter_exit);
}
