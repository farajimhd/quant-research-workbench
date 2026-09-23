use super::*;
use crate::events::{Decimal, EventKind, Payload, SourceTime};
const SECOND: u64 = 1_000_000_000;
#[test]
fn account_playback_recovery_keeps_partial_journal_progress() {
    use crate::account_boundary::tests::receipt;
    use playback::{accounts::Run, Poll};
    let manifest = account_run_manifest();
    let hash = manifest.hash().unwrap();
    let pinned = crate::run_manifest::Pinned::new(manifest, &hash).unwrap();
    let mut scheduler = scheduler(10);
    let policy = std::sync::Arc::new(empty_quote_policy(1));
    scheduler.bind_quote_policy(policy.clone()).unwrap();
    let seed = scheduler.market.runtime.structure.seed_hash.clone();
    let config = scheduler.market.runtime.configuration_hash().to_owned();
    let context = "c".repeat(64);
    let restore = |bundle: &playback::accounts::checkpoint::Bundle,
                   receipts: &[&crate::strategy_transaction::Committed]| {
        Run::restore_checkpoint(
            bundle,
            &bundle.root.id,
            &pinned,
            &playback_catalog(),
            prepared_playback(),
            checkpoint::Request {
                context_hash: &context,
                run_id: "causal-offline-test",
                seed_hash: &seed,
                configuration_hash: &config,
                quote_policy: policy.clone(),
                maximum_pending: 10,
                maximum_bytes: 10_000_000,
            },
            1,
            2,
            receipts,
        )
    };
    let mut original = Run::new(
        &pinned,
        &playback_catalog(),
        scheduler,
        prepared_playback(),
        1,
        2,
    )
    .unwrap();
    let mut recovered = restore(&original.checkpoint(&context, 10_000_000).unwrap(), &[]).unwrap();
    original.resume().unwrap();
    recovered.resume().unwrap();
    let mut boundaries = 0;
    loop {
        let poll = original.poll().unwrap();
        assert_eq!(recovered.poll().unwrap(), poll);
        if poll == Poll::Boundary {
            let input = original
                .pending()
                .unwrap()
                .unwrap()
                .input("test-features".into());
            let a = receipt(original.scopes()[0].clone(), input.clone());
            let b = receipt(original.scopes()[1].clone(), input);
            original.record(&a).unwrap();
            recovered.record(&a).unwrap();
            let image = recovered.checkpoint(&context, 10_000_000).unwrap();
            assert_eq!(
                image.root.id,
                original.checkpoint(&context, 10_000_000).unwrap().root.id
            );
            assert!(restore(&image, &[]).is_err());
            recovered = restore(&image, &[&a]).unwrap();
            assert_eq!(recovered.status().mode, playback::Mode::Paused);
            assert_eq!(recovered.remaining(), Some(1));
            assert!(!recovered.needs_decision(&recovered.scopes()[0]).unwrap());
            assert!(recovered.needs_decision(&recovered.scopes()[1]).unwrap());
            assert!(recovered.acknowledge().is_err());
            original.record(&b).unwrap();
            recovered.record(&b).unwrap();
            recovered = restore(
                &recovered.checkpoint(&context, 10_000_000).unwrap(),
                &[&b, &a],
            )
            .unwrap();
            original.acknowledge().unwrap();
            recovered.acknowledge().unwrap();
            recovered.resume().unwrap();
            boundaries += 1;
        } else {
            recovered = restore(&recovered.checkpoint(&context, 10_000_000).unwrap(), &[]).unwrap();
            if poll == Poll::Complete {
                break;
            }
            recovered.resume().unwrap();
        }
    }
    assert!(boundaries > 0);
    assert_eq!(
        original.checkpoint(&context, 10_000_000).unwrap().root.id,
        recovered.checkpoint(&context, 10_000_000).unwrap().root.id
    );
}
#[test]
fn playback_recovery_preserves_cursor_and_pending_boundaries() {
    use playback::{Mode, Playback, Poll};
    let mut scheduler = scheduler(10);
    let policy = std::sync::Arc::new(empty_quote_policy(1));
    scheduler.bind_quote_policy(policy.clone()).unwrap();
    let seed = scheduler.market.runtime.structure.seed_hash.clone();
    let config = scheduler.market.runtime.configuration_hash().to_owned();
    let prepared = prepared_playback();
    let mut original = Playback::new(scheduler, prepared.clone(), 1).unwrap();
    let context = "b".repeat(64);
    let restore = |bundle: &playback::checkpoint::Bundle, source: playback::Prepared| {
        Playback::restore_checkpoint(
            bundle,
            &bundle.root.id,
            source,
            checkpoint::Request {
                context_hash: &context,
                run_id: "causal-offline-test",
                seed_hash: &seed,
                configuration_hash: &config,
                quote_policy: policy.clone(),
                maximum_pending: 10,
                maximum_bytes: 10_000_000,
            },
            1,
        )
    };
    let mut recovered = restore(
        &original.checkpoint(&context, 10_000_000).unwrap(),
        prepared.clone(),
    )
    .unwrap();
    assert_eq!(recovered.poll().unwrap(), Poll::Paused);
    original.resume().unwrap();
    recovered.resume().unwrap();
    loop {
        let result = original.poll().unwrap();
        assert_eq!(recovered.poll().unwrap(), result);
        let image = recovered.checkpoint(&context, 10_000_000).unwrap();
        assert_eq!(
            image.root.id,
            original.checkpoint(&context, 10_000_000).unwrap().root.id
        );
        recovered = restore(&image, prepared.clone()).unwrap();
        if result == Poll::Complete {
            assert_eq!(recovered.status().mode, Mode::Complete);
            assert_eq!(recovered.poll().unwrap(), Poll::Complete);
            break;
        }
        assert_eq!(recovered.status().mode, Mode::Paused);
        if result == Poll::Boundary {
            assert_eq!(recovered.poll().unwrap(), Poll::Boundary);
            let id = original.pending().unwrap().unwrap().id.to_owned();
            assert_eq!(recovered.pending().unwrap().unwrap().id, id);
            original.acknowledge(&id).unwrap();
            recovered.acknowledge(&id).unwrap();
        }
        recovered.resume().unwrap();
    }
    let status = original.status();
    for (from, to) in [
        (
            format!("\"frame\":{}", status.completed_frames),
            "\"frame\":999999".into(),
        ),
        (
            format!("\"acknowledged\":{}", status.acknowledged_boundaries),
            "\"acknowledged\":999999".into(),
        ),
        (
            format!("\"admitted_events\":{}", status.admitted_events),
            "\"admitted_events\":999999".into(),
        ),
        (
            format!("\"prepared_hash\":\"{}\"", prepared.hash()),
            format!("\"prepared_hash\":\"{}\"", "0".repeat(64)),
        ),
    ] {
        let mut image = original.checkpoint(&context, 10_000_000).unwrap();
        let original_text = String::from_utf8(image.root.payload).unwrap();
        let text = original_text.replace(&from, &to);
        assert_ne!(original_text, text);
        image.root = crate::seed_storage::Object::new(text.into_bytes());
        assert!(restore(&image, prepared.clone()).is_err());
    }
}
#[test]
fn recovery_at_every_boundary_preserves_mixed_stream_and_unacknowledged_heads() {
    use checkpoint::Request;
    let runtime = super::super::tests::runtime_with_timeframes(
        20,
        vec![super::super::Timeframe {
            interval_ns: 5 * SECOND,
            macd_periods: (2, 3, 2),
            maximum_bars: 20,
        }],
    );
    let seed = runtime.structure.seed_hash.clone();
    let config = runtime.configuration_hash().to_owned();
    let mut original =
        Scheduler::new(Ordered::new(runtime, 10).unwrap(), "recovery-test".into()).unwrap();
    let policy = std::sync::Arc::new(empty_quote_policy(1));
    original.bind_quote_policy(policy.clone()).unwrap();
    for (e, eligible) in [
        (replay_quote(1, 200), false),
        (event(2, 200, 10), true),
        (event(3, 204, 12), true),
        (replay_quote(4, 205), false),
        (event(5, 205, 13), true),
    ] {
        original.enqueue(&e, eligible).unwrap();
    }
    let context = "a".repeat(64);
    let restore = |bundle: &checkpoint::Bundle| {
        Scheduler::restore_checkpoint(
            bundle,
            &bundle.root.id,
            Request {
                context_hash: &context,
                run_id: "recovery-test",
                seed_hash: &seed,
                configuration_hash: &config,
                quote_policy: policy.clone(),
                maximum_pending: 10,
                maximum_bytes: 10_000_000,
            },
        )
        .unwrap()
    };
    let mut recovered = restore(&original.checkpoint(&context, 10_000_000).unwrap());
    let mut count = 0;
    loop {
        // Different evaluation clocks also exercise already-computed bars waiting
        // in the completion queue rather than recalculating their availability.
        let at = 206 * SECOND + count;
        let more = original.prepare_next(206 * SECOND, at).unwrap();
        assert_eq!(recovered.prepare_next(206 * SECOND, at).unwrap(), more);
        assert_eq!(
            original.checkpoint(&context, 10_000_000).unwrap().root.id,
            recovered.checkpoint(&context, 10_000_000).unwrap().root.id
        );
        if !more {
            break;
        }
        let image = recovered.checkpoint(&context, 10_000_000).unwrap();
        let id = original.pending().unwrap().unwrap().id.to_owned();
        recovered = restore(&image);
        assert_eq!(recovered.pending().unwrap().unwrap().id, id);
        assert!(recovered.acknowledge("wrong").is_err());
        original.acknowledge(&id).unwrap();
        recovered.acknowledge(&id).unwrap();
        count += 1;
    }
    assert!(count > 7);
    assert!(!recovered.enqueue(&replay_quote(1, 200), false).unwrap());
    assert_eq!(recovered.pending_events(), 0);
}
#[test]
fn recovery_rejects_changed_pending_identity_and_external_pins() {
    let mut scheduler = scheduler(10);
    let policy = std::sync::Arc::new(empty_quote_policy(1));
    scheduler.bind_quote_policy(policy.clone()).unwrap();
    scheduler.enqueue(&event(1, 200, 10), true).unwrap();
    scheduler.prepare_next(201 * SECOND, 201 * SECOND).unwrap();
    let context = "a".repeat(64);
    let seed = scheduler.market.runtime.structure.seed_hash.clone();
    let config = scheduler.market.runtime.configuration_hash().to_owned();
    let mut image = scheduler.checkpoint(&context, 10_000_000).unwrap();
    let request = || checkpoint::Request {
        context_hash: &context,
        run_id: "causal-offline-test",
        seed_hash: &seed,
        configuration_hash: &config,
        quote_policy: policy.clone(),
        maximum_pending: 10,
        maximum_bytes: 10_000_000,
    };
    assert!(Scheduler::restore_checkpoint(&image, "wrong", request()).is_err());
    let wrong = checkpoint::Request {
        maximum_pending: 9,
        ..request()
    };
    assert!(Scheduler::restore_checkpoint(&image, &image.root.id, wrong).is_err());
    let id = scheduler.pending().unwrap().unwrap().id;
    let text = String::from_utf8(image.root.payload)
        .unwrap()
        .replace(id, &"0".repeat(64));
    image.root = crate::seed_storage::Object::new(text.into_bytes());
    assert!(Scheduler::restore_checkpoint(&image, &image.root.id, request()).is_err());
}
fn replay_quote(sequence: u64, second: u64) -> crate::events::Observation {
    let mut quote = event(sequence, second, 10);
    quote.key.kind = EventKind::Quote;
    quote.payload = Payload::Quote {
        bid: Decimal {
            atoms: 999,
            scale: 2,
        },
        ask: Decimal {
            atoms: 1001,
            scale: 2,
        },
        bid_size: Decimal {
            atoms: 10,
            scale: 0,
        },
        ask_size: Decimal {
            atoms: 10,
            scale: 0,
        },
        bid_exchange: 1,
        ask_exchange: 1,
        conditions: vec![],
        indicators: vec![],
    };
    quote
}
#[test]
fn quotes_and_trades_share_causal_boundaries_without_future_quote_leakage() {
    let event_signal = crate::execution_interval::ExecutionContract {
        kind: crate::execution_interval::ExecutableKind::SignalStream,
        id: "event-signal".into(),
        implementation_hash: "a".repeat(64),
        interval: crate::execution_interval::ExecutionInterval::Events,
    };
    let event_route = crate::execution_interval::Route::new(&event_signal).unwrap();
    let mut scheduler = scheduler(10);
    let first = replay_quote(1, 200);
    for (event, eligible) in [
        (event(4, 201, 20), true),
        (replay_quote(3, 201), false),
        (event(2, 200, 10), true),
        (first.clone(), false),
    ] {
        scheduler.enqueue(&event, eligible).unwrap();
    }
    assert!(scheduler.quotes().unwrap().latest().is_none());
    let mut seen = vec![];
    while scheduler.prepare_next(202 * SECOND, 202 * SECOND).unwrap() {
        let boundary = scheduler.pending().unwrap().unwrap();
        assert_eq!(
            boundary.due_for(event_route),
            !matches!(boundary.kind, Kind::Completed { .. })
        );
        let id = boundary.id.to_owned();
        match boundary.kind {
            Kind::Quote { observation } => {
                assert_eq!(
                    scheduler.quotes().unwrap().latest().unwrap().key,
                    observation.key
                );
                seen.push(("quote", observation.sip.ns / SECOND));
            }
            Kind::Trade { observation, .. } => {
                assert!(scheduler.quotes().unwrap().latest().unwrap().sip.ns <= observation.sip.ns);
                seen.push(("trade", observation.sip.ns / SECOND));
            }
            Kind::Completed { bar, .. } => {
                if bar.bar.end_ns == 201 * SECOND {
                    assert_eq!(
                        scheduler.quotes().unwrap().latest().unwrap().sip.ns,
                        first.sip.ns
                    );
                }
                seen.push(("bar", bar.bar.end_ns / SECOND));
            }
        }
        assert!(scheduler.prepare_next(202 * SECOND, 202 * SECOND).is_err());
        scheduler.acknowledge(&id).unwrap();
    }
    assert_eq!(
        seen,
        vec![
            ("quote", 200),
            ("trade", 200),
            ("bar", 201),
            ("quote", 201),
            ("trade", 201),
            ("bar", 202)
        ]
    );
    assert_eq!(scheduler.pending_events(), 0);
    assert!(!scheduler.enqueue(&first, false).unwrap());
    let mut changed = first;
    changed.sip.ns += 1;
    assert!(scheduler.enqueue(&changed, false).is_err());
    assert!(scheduler.quotes().is_err());
}
#[test]
fn playback_accepts_quotes_only_without_trade_eligibility() {
    use playback::{Frame, Input, Limits, Playback, Poll, Prepared};
    let prepare = |eligible| {
        Prepared::new(
            scheduler(10).scope(),
            "merged-source-v1",
            vec![Frame {
                watermark_ns: 201 * SECOND,
                evaluated_at_ns: 201 * SECOND,
                inputs: vec![Input {
                    observation: replay_quote(1, 200),
                    eligible,
                }],
            }],
            Limits {
                maximum_frames: 1,
                maximum_events: 1,
                maximum_serialized_bytes: 10000,
            },
        )
    };
    assert!(prepare(true).is_err());
    let mut replay = Playback::new(scheduler(10), prepare(false).unwrap(), 1).unwrap();
    replay.resume().unwrap();
    assert_eq!(replay.poll().unwrap(), Poll::Boundary);
    let boundary = replay.pending().unwrap().unwrap();
    assert!(matches!(boundary.kind, Kind::Quote { .. }));
    let id = boundary.id.to_owned();
    replay.acknowledge(&id).unwrap();
    while replay.poll().unwrap() == Poll::Yield {}
    assert_eq!(replay.status().mode, playback::Mode::Complete);
}
fn playback_catalog() -> playback::sources::Catalog {
    let scope = scheduler(10).scope();
    playback::sources::Catalog {
        schema_version: 1,
        authority_manifest_hash: "b".repeat(64),
        clock: crate::run_manifest::Clock::Historical,
        shards: vec![playback::sources::Shard {
            provider: scope.provider,
            instrument: scope.instrument,
            session: scope.session,
            prepared_hash: prepared_playback().hash().into(),
            clock_model: "historical-explicit-clock-v1".into(),
        }],
    }
}
fn account_run_manifest() -> crate::run_manifest::Manifest {
    use crate::run_manifest::{Clock, Consumer, Execution, Manifest};
    Manifest {
        schema_version: 1,
        run_id: "causal-offline-test".into(),
        mode: crate::strategy_dispatch::Mode::Backtest,
        code_release_hash: "a".repeat(64),
        source_manifest_hash: playback_catalog().hash().unwrap(),
        reference_manifest_hash: "c".repeat(64),
        seed_manifest_hash: "d".repeat(64),
        algorithm_manifest_hash: "e".repeat(64),
        dependency_plan_hash: "f".repeat(64),
        hardware_profile_hash: "1".repeat(64),
        clock: Clock::Historical,
        execution: Execution::Simulated {
            fill_model_hash: "2".repeat(64),
            cost_model_hash: "3".repeat(64),
        },
        consumers: ["a", "b"]
            .into_iter()
            .map(|account| Consumer {
                account: account.into(),
                instrument: 1,
                strategy_instance: "candidate".into(),
                effective_config_hash: "4".repeat(64),
            })
            .collect(),
    }
}
#[test]
fn manifest_playback_requires_all_account_receipts_before_advancing() {
    use crate::account_boundary::tests::receipt;
    use playback::{accounts::Run, Poll};
    let m = account_run_manifest();
    let hash = m.hash().unwrap();
    let pinned = crate::run_manifest::Pinned::new(m, &hash).unwrap();
    let market = super::super::tests::runtime_with_timeframes(
        100,
        vec![super::super::Timeframe {
            interval_ns: 5 * SECOND,
            macd_periods: (12, 26, 9),
            maximum_bars: 20,
        }],
    );
    let scheduler = Scheduler::new(
        Ordered::new(market, 10).unwrap(),
        "causal-offline-test".into(),
    )
    .unwrap();
    let mut run = Run::new(
        &pinned,
        &playback_catalog(),
        scheduler,
        prepared_playback(),
        1,
        2,
    )
    .unwrap();
    let mut features = crate::candidate_features::State::new(
        run.market().unwrap(),
        crate::candidate_features::Config {
            swings: crate::local_swings::Config {
                reversal_bps: 50.,
                volatility_multiple: 2.,
                volatility_cap_multiple: 2.,
                lifetime_bars: 1800,
                maximum_levels: 100,
            },
            encounters: crate::strategy_encounters::stream::Config {
                tick: 0.01,
                settings: crate::strategy_encounters::Settings {
                    breakout_buffer_ticks: 1.,
                    breakout_buffer_bps: 0.,
                    rejection_break_offset_bps: 10.,
                    topping_tail_fraction: 0.5,
                    maximum_encounters: 100,
                },
                maximum_prior_levels: 100,
            },
            setup: crate::strategy_setup::SetupSettings {
                range_ns: 30 * SECOND,
                minimum_bars: 1,
                maximum_gap_ns: 0,
            },
            forming_macd: true,
            minimum_range_pct: 0.,
            minimum_progress_pct: 0.,
            maximum_quote_age_ns: SECOND,
            maximum_completed_bar_age_ns: SECOND,
            maximum_levels: 100,
        },
    )
    .unwrap();
    assert_eq!(run.manifest_hash(), hash);
    run.resume().unwrap();
    let mut count = 0;
    loop {
        match run.poll().unwrap() {
            Poll::Boundary => {
                assert!(run.observe_features(&mut features).unwrap());
                assert!(!run.observe_features(&mut features).unwrap());
                let boundary = run.pending().unwrap().unwrap();
                if let Kind::Trade { observation, .. } = boundary.kind {
                    let context = || crate::candidate_features::AcquisitionContext {
                        tradable: true,
                        regular_block: false,
                        encounter_blocked: true,
                        pending_capital: false,
                    };
                    let mut quotes =
                        crate::quote_state::Book::new(run.market().unwrap().source_scope())
                            .unwrap();
                    quotes.bind_policy(empty_quote_policy(1)).unwrap();
                    assert!(features
                        .acquisition_frame(&boundary, run.market().unwrap(), &quotes, context())
                        .is_err());
                    let mut quote = observation.clone();
                    quote.key.kind = EventKind::Quote;
                    quote.sip.ns = boundary.evaluated_at_ns;
                    quote.available_at_ns = boundary.evaluated_at_ns;
                    quote.payload = Payload::Quote {
                        bid: Decimal {
                            atoms: 1000,
                            scale: 2,
                        },
                        ask: Decimal {
                            atoms: 1001,
                            scale: 2,
                        },
                        bid_size: Decimal {
                            atoms: 10,
                            scale: 0,
                        },
                        ask_size: Decimal {
                            atoms: 10,
                            scale: 0,
                        },
                        bid_exchange: 1,
                        ask_exchange: 1,
                        conditions: vec![],
                        indicators: vec![],
                    };
                    quotes.observe(&quote).unwrap();
                    let (frame, high) = features
                        .acquisition_frame(&boundary, run.market().unwrap(), &quotes, context())
                        .unwrap();
                    let Payload::Trade { price, .. } = observation.payload else {
                        panic!("trade required")
                    };
                    assert_eq!(frame.price, price.to_f64());
                    assert_eq!(frame.ask, 10.01);
                    assert_eq!(frame.at_ns, boundary.evaluated_at_ns);
                    assert_eq!(frame.quote_policy_hash, quotes.policy_hash().unwrap());
                    assert!(frame.encounter_blocked);
                    let bar = run
                        .market()
                        .unwrap()
                        .market()
                        .unwrap()
                        .developing()
                        .unwrap();
                    assert_eq!(high, bar.open.max(bar.close));
                    let wrong = Boundary {
                        id: "wrong",
                        sequence: boundary.sequence,
                        evaluated_at_ns: boundary.evaluated_at_ns,
                        kind: Kind::Trade {
                            observation,
                            eligible: true,
                        },
                    };
                    assert!(features
                        .acquisition_frame(&wrong, run.market().unwrap(), &quotes, context())
                        .is_err());
                }
                let input = run.pending().unwrap().unwrap().input("features".into());
                let scopes = run.scopes().to_vec();
                let candidate = crate::candidate_runtime::Runtime::new(
                    scopes[0].clone(),
                    crate::strategy_candidate::State::default(),
                    10000,
                )
                .unwrap();
                assert_eq!(candidate.scope(), &scopes[0]);
                assert_eq!(run.remaining(), Some(2));
                assert!(run.acknowledge().is_err());
                let a = receipt(scopes[0].clone(), input.clone());
                assert!(run.record(&a).unwrap());
                assert!(!run.record(&a).unwrap());
                assert!(!run.needs_decision(&scopes[0]).unwrap());
                assert!(run.needs_decision(&scopes[1]).unwrap());
                assert!(run.acknowledge().is_err());
                assert_eq!(run.poll().unwrap(), Poll::Boundary);
                assert_eq!(run.pending().unwrap().unwrap().id, input.event_id);
                let mut foreign = scopes[1].clone();
                foreign.account = "undeclared".into();
                assert!(run.record(&receipt(foreign, input.clone())).is_err());
                run.record(&receipt(scopes[1].clone(), input)).unwrap();
                run.acknowledge().unwrap();
                assert_eq!(run.remaining(), None);
                count += 1;
            }
            Poll::Yield => {}
            Poll::Complete => break,
            Poll::Paused => panic!("unexpected pause"),
        }
    }
    assert!(count >= 4);
    assert_eq!(run.status().acknowledged_boundaries, count);
}
#[test]
fn manifest_playback_rejects_run_mismatch_missing_consumers_and_capacity() {
    use playback::accounts::Run;
    let mut m = account_run_manifest();
    let pinned = crate::run_manifest::Pinned::new(m.clone(), &m.hash().unwrap()).unwrap();
    assert!(Run::new(
        &pinned,
        &playback_catalog(),
        scheduler(10),
        prepared_playback(),
        1,
        1
    )
    .is_err());
    m.run_id = "other".into();
    let pinned = crate::run_manifest::Pinned::new(m.clone(), &m.hash().unwrap()).unwrap();
    assert!(Run::new(
        &pinned,
        &playback_catalog(),
        scheduler(10),
        prepared_playback(),
        1,
        2
    )
    .is_err());
    m.run_id = "causal-offline-test".into();
    for row in &mut m.consumers {
        row.instrument = 999;
    }
    let pinned = crate::run_manifest::Pinned::new(m.clone(), &m.hash().unwrap()).unwrap();
    assert!(Run::new(
        &pinned,
        &playback_catalog(),
        scheduler(10),
        prepared_playback(),
        1,
        2
    )
    .is_err());
}
#[test]
fn playback_source_binding_rejects_changed_data_clock_and_scope() {
    let mut catalog = playback_catalog();
    let mut m = account_run_manifest();
    let pinned = crate::run_manifest::Pinned::new(m.clone(), &m.hash().unwrap()).unwrap();
    catalog.require(&pinned, &prepared_playback()).unwrap();
    catalog.shards[0].prepared_hash = "0".repeat(64);
    assert!(catalog.require(&pinned, &prepared_playback()).is_err());
    m.source_manifest_hash = catalog.hash().unwrap();
    let pinned = crate::run_manifest::Pinned::new(m.clone(), &m.hash().unwrap()).unwrap();
    assert!(catalog.require(&pinned, &prepared_playback()).is_err());
    catalog = playback_catalog();
    catalog.shards[0].clock_model = "different-latency-model".into();
    m.source_manifest_hash = catalog.hash().unwrap();
    let pinned = crate::run_manifest::Pinned::new(m.clone(), &m.hash().unwrap()).unwrap();
    assert!(catalog.require(&pinned, &prepared_playback()).is_err());
    catalog = playback_catalog();
    catalog.shards[0].instrument = 999;
    m.source_manifest_hash = catalog.hash().unwrap();
    let pinned = crate::run_manifest::Pinned::new(m.clone(), &m.hash().unwrap()).unwrap();
    assert!(catalog.require(&pinned, &prepared_playback()).is_err());
    catalog = playback_catalog();
    catalog.clock = crate::run_manifest::Clock::RecordedLive;
    m.source_manifest_hash = catalog.hash().unwrap();
    let pinned = crate::run_manifest::Pinned::new(m.clone(), &m.hash().unwrap()).unwrap();
    assert!(catalog.require(&pinned, &prepared_playback()).is_err());
    catalog.shards.push(catalog.shards[0].clone());
    assert!(catalog.hash().is_err());
}
#[test]
fn recorded_playback_requires_captured_receipts_and_preserves_lane_order() {
    use playback::{Frame, Input, Limits, Prepared};
    let check = |events: Vec<crate::events::Observation>| {
        let prepared = Prepared::new(
            scheduler(10).scope(),
            "captured-v1",
            vec![Frame {
                watermark_ns: 204 * SECOND,
                evaluated_at_ns: 204 * SECOND,
                inputs: events
                    .into_iter()
                    .map(|observation| Input {
                        observation,
                        eligible: true,
                    })
                    .collect(),
            }],
            Limits {
                maximum_frames: 1,
                maximum_events: 10,
                maximum_serialized_bytes: 10000,
            },
        )
        .unwrap();
        let mut catalog = playback_catalog();
        catalog.clock = crate::run_manifest::Clock::RecordedLive;
        catalog.shards[0].prepared_hash = prepared.hash().into();
        catalog.shards[0].clock_model = "captured-v1".into();
        let mut manifest = account_run_manifest();
        manifest.clock = catalog.clock.clone();
        manifest.source_manifest_hash = catalog.hash().unwrap();
        let pinned =
            crate::run_manifest::Pinned::new(manifest.clone(), &manifest.hash().unwrap()).unwrap();
        catalog.require(&pinned, &prepared)
    };
    let mut a = event(1, 200, 10);
    assert!(check(vec![a.clone()]).is_err());
    a.receipt = Some(crate::events::Receipt {
        run_id: "capture".into(),
        lane: 0,
        sequence: 7,
        utc_ns: a.available_at_ns,
        monotonic_ns: 10,
    });
    let mut b = event(2, 203, 20);
    b.receipt = Some(crate::events::Receipt {
        run_id: "capture".into(),
        lane: 0,
        sequence: 9,
        utc_ns: b.available_at_ns,
        monotonic_ns: 10,
    });
    assert!(check(vec![a.clone(), b.clone()]).is_ok());
    assert!(check(vec![a.clone(), a.clone(), b.clone()]).is_ok());
    b.receipt.as_mut().unwrap().sequence = 7;
    assert!(check(vec![a.clone(), b.clone()]).is_err());
    b.receipt.as_mut().unwrap().sequence = 9;
    b.receipt.as_mut().unwrap().monotonic_ns = 9;
    assert!(check(vec![a.clone(), b.clone()]).is_err());
    b.receipt.as_mut().unwrap().lane = 1;
    assert!(check(vec![a.clone(), b]).is_ok());
    a.receipt.as_mut().unwrap().sequence = 0;
    assert!(check(vec![a]).is_err());
}
fn empty_quote_policy(provider: u16) -> crate::quote_state::eligibility::Pinned {
    use crate::quote_state::eligibility::{Pinned, Policy};
    let p = Policy {
        provider,
        valid_from_ns: 0,
        valid_to_ns: u64::MAX,
        available_at_ns: 0,
        source_manifest_hash: "a".repeat(64),
        allowed_conditions: Default::default(),
        allowed_indicators: Default::default(),
        allow_empty_conditions: true,
        allow_empty_indicators: true,
    };
    let hash = crate::content_hash(&p).unwrap();
    Pinned::new(p, &hash).unwrap()
}
fn prepared_playback() -> playback::Prepared {
    use playback::{Frame, Input, Limits, Prepared};
    Prepared::new(
        scheduler(10).scope(),
        "historical-explicit-clock-v1",
        vec![
            Frame {
                watermark_ns: 201 * SECOND,
                evaluated_at_ns: 201 * SECOND,
                inputs: vec![Input {
                    observation: event(1, 200, 10),
                    eligible: true,
                }],
            },
            Frame {
                watermark_ns: 204 * SECOND,
                evaluated_at_ns: 204 * SECOND,
                inputs: vec![Input {
                    observation: event(2, 203, 20),
                    eligible: true,
                }],
            },
        ],
        Limits {
            maximum_frames: 10,
            maximum_events: 10,
            maximum_serialized_bytes: 10000,
        },
    )
    .unwrap()
}
#[test]
fn playback_reports_coalescing_without_replaying_duplicate_trade() {
    use playback::{Frame, Input, Limits, Playback, Poll, Prepared};
    let e = Input {
        observation: event(1, 200, 10),
        eligible: true,
    };
    let source = Prepared::new(
        scheduler(10).scope(),
        "explicit",
        vec![Frame {
            watermark_ns: 201 * SECOND,
            evaluated_at_ns: 201 * SECOND,
            inputs: vec![e.clone(), e],
        }],
        Limits {
            maximum_frames: 1,
            maximum_events: 2,
            maximum_serialized_bytes: 10000,
        },
    )
    .unwrap();
    let mut run = Playback::new(scheduler(10), source, 1).unwrap();
    run.resume().unwrap();
    assert_eq!(run.poll().unwrap(), Poll::Boundary);
    assert_eq!(run.status().admitted_events, 1);
    assert_eq!(run.status().coalesced_events, 1);
    assert_eq!(run.status().queued_events, 1);
    let id = run.pending().unwrap().unwrap().id.to_owned();
    run.acknowledge(&id).unwrap();
    assert_eq!(run.status().queued_events, 0);
    assert_eq!(run.poll().unwrap(), Poll::Boundary);
    assert!(matches!(
        run.pending().unwrap().unwrap().kind,
        Kind::Completed { .. }
    ));
}
#[test]
fn playback_step_pause_and_resume_preserve_identical_causal_results() {
    use playback::{Mode, Playback, Poll};
    let prepared = prepared_playback();
    let mut normal = Playback::new(scheduler(10), prepared.clone(), 1).unwrap();
    let mut stepped = Playback::new(scheduler(10), prepared, 1).unwrap();
    assert_eq!(stepped.poll().unwrap(), Poll::Paused);
    normal.resume().unwrap();
    let mut expected = vec![];
    loop {
        match normal.poll().unwrap() {
            Poll::Boundary => {
                let boundary = normal.pending().unwrap().unwrap();
                if let Kind::Trade { observation, .. } = boundary.kind {
                    assert!(observation.receipt.is_none());
                    assert!(observation.participant.is_none());
                }
                let id = boundary.id.to_owned();
                expected.push(id.clone());
                normal.acknowledge(&id).unwrap();
            }
            Poll::Yield => {}
            Poll::Complete => break,
            Poll::Paused => panic!("unexpected pause"),
        }
    }
    let mut actual = vec![];
    loop {
        stepped.step().unwrap();
        loop {
            match stepped.poll().unwrap() {
                Poll::Yield => continue,
                Poll::Boundary => {
                    let id = stepped.pending().unwrap().unwrap().id.to_owned();
                    let hash = stepped.market().unwrap().checkpoint().unwrap().hash;
                    assert_eq!(stepped.status().mode, Mode::Paused);
                    assert!(stepped.step().is_err());
                    assert!(stepped.acknowledge("wrong").is_err());
                    stepped.resume().unwrap();
                    stepped.pause().unwrap();
                    assert_eq!(stepped.poll().unwrap(), Poll::Boundary);
                    assert_eq!(stepped.market().unwrap().checkpoint().unwrap().hash, hash);
                    actual.push(id.clone());
                    stepped.acknowledge(&id).unwrap();
                    assert_eq!(stepped.poll().unwrap(), Poll::Paused);
                    break;
                }
                Poll::Complete => break,
                Poll::Paused => panic!("step did not advance"),
            }
        }
        if stepped.status().mode == Mode::Complete {
            break;
        }
    }
    assert_eq!(expected, actual);
    assert_eq!(actual.len(), 4);
    assert_eq!(
        normal.market().unwrap().checkpoint().unwrap().hash,
        stepped.market().unwrap().checkpoint().unwrap().hash
    );
    assert_eq!(stepped.status().acknowledged_boundaries, 4);
    assert_eq!(stepped.status().completed_frames, 2);
    assert!(stepped.resume().is_err());
}
#[test]
fn playback_validates_prepared_scope_clocks_and_capacity() {
    use playback::{Frame, Input, Limits, Prepared};
    let scope = scheduler(10).scope();
    let valid = Frame {
        watermark_ns: 201 * SECOND,
        evaluated_at_ns: 201 * SECOND,
        inputs: vec![Input {
            observation: event(1, 200, 10),
            eligible: true,
        }],
    };
    let prepare = |frames, bytes| {
        Prepared::new(
            scope,
            "explicit",
            frames,
            Limits {
                maximum_frames: 3,
                maximum_events: 3,
                maximum_serialized_bytes: bytes,
            },
        )
    };
    assert!(prepare(vec![valid.clone()], 1).is_err());
    let mut future = valid.clone();
    future.inputs[0].observation.available_at_ns = 202 * SECOND;
    assert!(prepare(vec![future], 10000).is_err());
    let mut foreign = valid.clone();
    foreign.inputs[0].observation.key.instrument = 2;
    assert!(prepare(vec![foreign], 10000).is_err());
    assert!(prepare(vec![valid.clone(), valid.clone()], 10000).is_err());
    let mut rewound = valid.clone();
    rewound.watermark_ns -= 1;
    rewound.inputs.clear();
    assert!(prepare(vec![valid.clone(), rewound], 10000).is_err());
    let original = prepare(vec![valid.clone()], 10000).unwrap();
    let mut changed = valid;
    changed.inputs[0].eligible = false;
    assert_ne!(
        original.hash(),
        prepare(vec![changed], 10000).unwrap().hash()
    );
}
#[test]
fn playback_work_budget_yields_and_incomplete_watermark_fails_without_discarding() {
    use playback::{Frame, Input, Limits, Mode, Playback, Poll, Prepared};
    let scope = scheduler(10).scope();
    let pending = event(1, 201, 10);
    let prepared = Prepared::new(
        scope,
        "explicit",
        vec![
            Frame {
                watermark_ns: 200 * SECOND,
                evaluated_at_ns: 200 * SECOND,
                inputs: vec![],
            },
            Frame {
                watermark_ns: 201 * SECOND,
                evaluated_at_ns: 202 * SECOND,
                inputs: vec![Input {
                    observation: pending,
                    eligible: true,
                }],
            },
        ],
        Limits {
            maximum_frames: 2,
            maximum_events: 1,
            maximum_serialized_bytes: 10000,
        },
    )
    .unwrap();
    let mut runtime = Playback::new(scheduler(10), prepared, 1).unwrap();
    runtime.resume().unwrap();
    assert_eq!(runtime.poll().unwrap(), Poll::Yield);
    assert_eq!(runtime.status().admitted_events, 0);
    assert_eq!(runtime.poll().unwrap(), Poll::Yield);
    assert_eq!(runtime.status().admitted_events, 1);
    assert!(runtime.poll().is_err());
    assert_eq!(runtime.status().mode, Mode::Failed);
    assert!(runtime
        .status()
        .failure
        .unwrap()
        .contains("final watermark"));
    assert!(runtime.resume().is_err());
    assert_eq!(runtime.status().acknowledged_boundaries, 0);
}
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
    use crate::execution_interval::{ExecutableKind, ExecutionContract, ExecutionInterval};
    let event_signal = ExecutionContract {
        kind: ExecutableKind::SignalStream,
        id: "event-signal".into(),
        implementation_hash: "a".repeat(64),
        interval: ExecutionInterval::Events,
    };
    let bar_signal = ExecutionContract {
        kind: ExecutableKind::SignalStream,
        id: "bar-signal".into(),
        implementation_hash: "b".repeat(64),
        interval: ExecutionInterval::Fixed(SECOND),
    };
    let other_bar_signal = ExecutionContract {
        interval: ExecutionInterval::Fixed(2 * SECOND),
        ..bar_signal.clone()
    };
    let event_route = crate::execution_interval::Route::new(&event_signal).unwrap();
    let bar_route = crate::execution_interval::Route::new(&bar_signal).unwrap();
    let other_bar_route = crate::execution_interval::Route::new(&other_bar_signal).unwrap();
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
        let is_completed = matches!(boundary.kind, Kind::Completed { .. });
        assert_eq!(boundary.due_for(event_route), !is_completed);
        assert_eq!(boundary.due_for(bar_route), is_completed);
        assert!(!boundary.due_for(other_bar_route));
        match boundary.kind {
            Kind::Quote { .. } => panic!("trade-only fixture"),
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
    let config = crate::candidate_features::Config {
        swings: crate::local_swings::Config {
            reversal_bps: 50.,
            volatility_multiple: 2.,
            volatility_cap_multiple: 2.,
            lifetime_bars: 1800,
            maximum_levels: 100,
        },
        encounters: crate::strategy_encounters::stream::Config {
            tick: 0.01,
            settings: crate::strategy_encounters::Settings {
                breakout_buffer_ticks: 1.,
                breakout_buffer_bps: 0.,
                rejection_break_offset_bps: 10.,
                topping_tail_fraction: 0.5,
                maximum_encounters: 100,
            },
            maximum_prior_levels: 100,
        },
        setup: crate::strategy_setup::SetupSettings {
            range_ns: 30 * SECOND,
            minimum_bars: 1,
            maximum_gap_ns: 10 * SECOND,
        },
        forming_macd: true,
        minimum_range_pct: 1.,
        minimum_progress_pct: 1.,
        maximum_quote_age_ns: SECOND,
        maximum_completed_bar_age_ns: SECOND,
        maximum_levels: 100,
    };
    let mut features =
        crate::candidate_features::State::new(scheduler.state().unwrap(), config.clone()).unwrap();
    let mut recovered =
        crate::candidate_features::State::new(scheduler.state().unwrap(), config.clone()).unwrap();
    let context = "f".repeat(64);
    let mut evaluated_at_ns = 220 * SECOND;
    while scheduler
        .prepare_next(220 * SECOND, evaluated_at_ns)
        .unwrap()
    {
        let boundary = scheduler.pending().unwrap().unwrap();
        assert!(features
            .observe(&boundary, scheduler.state().unwrap())
            .unwrap());
        assert!(recovered
            .observe(&boundary, scheduler.state().unwrap())
            .unwrap());
        let image = features
            .checkpoint(&context, scheduler.state().unwrap(), &boundary, 1_000_000)
            .unwrap();
        if matches!(
            boundary.kind,
            Kind::Completed {
                interval_ns: SECOND,
                ..
            }
        ) {
            assert_eq!(
                crate::content_hash(&features.completed_swings(&boundary).unwrap()).unwrap(),
                crate::content_hash(
                    &features
                        .snapshot()
                        .unwrap()
                        .unwrap()
                        .one_second
                        .as_ref()
                        .unwrap()
                        .swings
                        .swings
                )
                .unwrap()
            );
        } else {
            assert!(features.completed_swings(&boundary).is_err());
        }
        assert_eq!(
            image.id,
            recovered
                .checkpoint(&context, scheduler.state().unwrap(), &boundary, 1_000_000)
                .unwrap()
                .id
        );
        recovered = crate::candidate_features::State::restore_checkpoint(
            &image,
            &image.id,
            &context,
            scheduler.state().unwrap(),
            config.clone(),
            &boundary,
            1_000_000,
        )
        .unwrap();
        assert!(!recovered
            .observe(&boundary, scheduler.state().unwrap())
            .unwrap());
        let mut changed = config.clone();
        changed.minimum_range_pct += 1.;
        assert!(crate::candidate_features::State::restore_checkpoint(
            &image,
            &image.id,
            &context,
            scheduler.state().unwrap(),
            changed,
            &boundary,
            1_000_000
        )
        .is_err());
        let mut changed = config.clone();
        changed.swings.reversal_bps += 1.;
        assert!(crate::candidate_features::State::restore_checkpoint(
            &image,
            &image.id,
            &context,
            scheduler.state().unwrap(),
            changed,
            &boundary,
            1_000_000
        )
        .is_err());
        let mut changed = config.clone();
        changed.encounters.settings.breakout_buffer_ticks += 1.;
        assert!(crate::candidate_features::State::restore_checkpoint(
            &image,
            &image.id,
            &context,
            scheduler.state().unwrap(),
            changed,
            &boundary,
            1_000_000
        )
        .is_err());
        assert!(features
            .checkpoint(&context, scheduler.state().unwrap(), &boundary, 1)
            .is_err());
        let market_hash = scheduler.state().unwrap().checkpoint().unwrap().hash;
        let text = String::from_utf8(image.payload.clone()).unwrap();
        let changed = text.replace(&market_hash, &"0".repeat(64));
        assert_ne!(text, changed);
        let wrong_market = crate::seed_storage::Object::new(changed.into_bytes());
        assert!(crate::candidate_features::State::restore_checkpoint(
            &wrong_market,
            &wrong_market.id,
            &context,
            scheduler.state().unwrap(),
            config.clone(),
            &boundary,
            1_000_000
        )
        .is_err());
        let snapshot_hash = crate::content_hash(features.snapshot().unwrap().unwrap()).unwrap();
        assert!(!features
            .observe(&boundary, scheduler.state().unwrap())
            .unwrap());
        assert_eq!(
            crate::content_hash(features.snapshot().unwrap().unwrap()).unwrap(),
            snapshot_hash
        );
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
                let one = features
                    .snapshot()
                    .unwrap()
                    .unwrap()
                    .one_second
                    .as_ref()
                    .unwrap();
                assert!(one.range.is_none());
                assert!(one.previous_bar_end_ns.is_none());
                assert_eq!(one.vwap, 10.);
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
                    let one = features
                        .snapshot()
                        .unwrap()
                        .unwrap()
                        .one_second
                        .as_ref()
                        .unwrap();
                    assert_eq!(one.range.as_ref().unwrap().high, 10.);
                    assert_eq!(one.prior_high, Some(10.));
                    assert_eq!(one.high, 20.);
                    assert_eq!(one.vwap, 15.);
                    assert!(one.previous_bar_end_ns.is_none());
                    assert!(!one.progress_activity.passed);
                    assert_eq!(
                        one.progress_activity.reason,
                        "historical_reference_missing_or_stale"
                    );
                    check_entry_frame(&features, &boundary, scheduler.state().unwrap());
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
                    assert!(features.snapshot().unwrap().unwrap().one_second.is_none());
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

#[test]
fn feature_owner_rejects_missing_dependencies_and_skipped_or_invalid_boundaries() {
    use crate::candidate_features::{Config, State};
    let config = || Config {
        swings: crate::local_swings::Config {
            reversal_bps: 50.,
            volatility_multiple: 2.,
            volatility_cap_multiple: 2.,
            lifetime_bars: 1800,
            maximum_levels: 100,
        },
        encounters: crate::strategy_encounters::stream::Config {
            tick: 0.01,
            settings: crate::strategy_encounters::Settings {
                breakout_buffer_ticks: 1.,
                breakout_buffer_bps: 0.,
                rejection_break_offset_bps: 10.,
                topping_tail_fraction: 0.5,
                maximum_encounters: 100,
            },
            maximum_prior_levels: 100,
        },
        setup: crate::strategy_setup::SetupSettings {
            range_ns: 30 * SECOND,
            minimum_bars: 1,
            maximum_gap_ns: 0,
        },
        forming_macd: true,
        minimum_range_pct: 0.,
        minimum_progress_pct: 0.,
        maximum_quote_age_ns: SECOND,
        maximum_completed_bar_age_ns: SECOND,
        maximum_levels: 100,
    };
    assert!(State::new(&super::super::tests::runtime(10), config()).is_err());
    let runtime = super::super::tests::runtime_with_timeframes(
        100,
        vec![super::super::Timeframe {
            interval_ns: 5 * SECOND,
            macd_periods: (12, 26, 9),
            maximum_bars: 20,
        }],
    );
    let mut invalid = config();
    invalid.setup.range_ns = 3601 * SECOND;
    assert!(State::new(&runtime, invalid).is_err());
    let mut scheduler =
        Scheduler::new(Ordered::new(runtime, 10).unwrap(), "feature-failure".into()).unwrap();
    let mut features = State::new(scheduler.state().unwrap(), config()).unwrap();
    scheduler.enqueue(&event(1, 200, 10), true).unwrap();
    scheduler.prepare_next(202 * SECOND, 202 * SECOND).unwrap();
    let boundary = scheduler.pending().unwrap().unwrap();
    let Kind::Trade {
        observation,
        eligible,
    } = boundary.kind
    else {
        unreachable!()
    };
    let mut supplied = Boundary {
        id: boundary.id,
        sequence: boundary.sequence + 1,
        evaluated_at_ns: boundary.evaluated_at_ns,
        kind: Kind::Trade {
            observation,
            eligible,
        },
    };
    assert!(features
        .observe(&supplied, scheduler.state().unwrap())
        .is_err());
    assert!(features.snapshot().is_err());
    supplied.sequence = boundary.sequence;
    supplied.evaluated_at_ns = 1;
    assert!(features
        .observe(&supplied, scheduler.state().unwrap())
        .is_err());
    assert!(features.snapshot().is_err());
    assert!(features
        .observe(&boundary, scheduler.state().unwrap())
        .is_err());
}

fn check_entry_frame(
    features: &crate::candidate_features::State,
    boundary: &Boundary<'_>,
    market: &Runtime,
) {
    use crate::candidate_features::EntryContext;
    let snapshot = features.snapshot().unwrap().unwrap();
    let one = snapshot.one_second.as_ref().unwrap();
    let reading = snapshot.macd.as_ref().unwrap();
    let admission = crate::strategy_entry::Admission {
        at_ns: one.at_ns,
        permissions: false,
        session_open: true,
        tradable: false,
        encounter_blocked: false,
        regular_block: None,
        macd_at_ns: Some(reading.at_ns),
        macd_positive: reading.positive(),
        detector_at_ns: None,
        detector_fingerprint: String::new(),
        activity_block: one.activity_block().map(str::to_owned),
    };
    let recovery = crate::strategy_lifecycle::RecoveryState::default();
    let recovery_policy = crate::strategy_lifecycle::RecoveryPolicy::default();
    let context = EntryContext {
        admission: &admission,
        swings: &[],
        regular: false,
        regular_target: None,
        recovery: &recovery,
        recovery_policy: &recovery_policy,
    };
    let mut quotes = crate::quote_state::Book::new(market.source_scope()).unwrap();
    quotes
        .bind_policy(empty_quote_policy(market.source_scope().provider))
        .unwrap();
    assert!(features
        .entry_frame(boundary, boundary.evaluated_at_ns, market, &quotes, context)
        .is_err());
    let quote = Observation {
        key: EventKey {
            provider: 1,
            instrument: 1,
            session: 20260915,
            kind: EventKind::Quote,
            sequence: 1,
        },
        payload: Payload::Quote {
            bid: Decimal {
                atoms: 1999,
                scale: 2,
            },
            ask: Decimal {
                atoms: 2001,
                scale: 2,
            },
            bid_size: Decimal {
                atoms: 100,
                scale: 0,
            },
            ask_size: Decimal {
                atoms: 100,
                scale: 0,
            },
            bid_exchange: 1,
            ask_exchange: 1,
            conditions: vec![],
            indicators: vec![],
        },
        sip: SourceTime {
            ns: boundary.evaluated_at_ns - 1,
            precision_ns: 1,
        },
        participant: None,
        available_at_ns: boundary.evaluated_at_ns,
        receipt: None,
    };
    quotes.observe(&quote).unwrap();
    let frame = features
        .entry_frame(boundary, boundary.evaluated_at_ns, market, &quotes, context)
        .unwrap();
    assert_eq!(frame.bar.end_ns, one.at_ns);
    assert_eq!(frame.vwap, Some(15.));
    assert_eq!(frame.bid, 19.99);
    assert_eq!(frame.ask, 20.01);
    assert_eq!(frame.quote_policy_hash, quotes.policy_hash().unwrap());
    let owned = features
        .owned_entry_frame(
            boundary,
            boundary.evaluated_at_ns,
            market,
            &quotes,
            crate::candidate_features::OwnedEntryContext {
                admission: &admission,
                regular: context.regular,
                regular_target: context.regular_target,
                recovery: &recovery,
                recovery_policy: &recovery_policy,
            },
        )
        .unwrap();
    assert_eq!(
        crate::content_hash(&owned.swings).unwrap(),
        crate::content_hash(&features.completed_swings(boundary).unwrap()).unwrap()
    );
    assert_eq!(
        owned.swings.as_ptr(),
        features.completed_swings(boundary).unwrap().as_ptr()
    );
    assert!(!owned.admission.permissions && !owned.admission.tradable);
    let mut wrong_boundary = Boundary {
        id: boundary.id,
        sequence: boundary.sequence + 1,
        evaluated_at_ns: boundary.evaluated_at_ns,
        kind: Kind::Completed {
            interval_ns: SECOND,
            bar: match &boundary.kind {
                Kind::Completed { bar, .. } => bar,
                _ => unreachable!(),
            },
            available_at_ns: boundary.evaluated_at_ns,
        },
    };
    assert!(features.completed_swings(&wrong_boundary).is_err());
    wrong_boundary.sequence = boundary.sequence;
    wrong_boundary.evaluated_at_ns += 1;
    assert!(features.completed_swings(&wrong_boundary).is_err());
    let changed_policy_frame = crate::strategy_entry::Frame {
        quote_policy_hash: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        ..frame
    };
    assert_ne!(
        crate::content_hash(&frame).unwrap(),
        crate::content_hash(&changed_policy_frame).unwrap()
    );
    assert!(!frame.fresh);
    assert!(!frame.admission.permissions);
    assert!(!frame.admission.tradable);
    assert!(frame.previous.is_none());
    assert_eq!(frame.range.unwrap().high, 10.);
    let mut contradictory = admission.clone();
    contradictory.macd_positive = !contradictory.macd_positive;
    assert!(features
        .entry_frame(
            boundary,
            boundary.evaluated_at_ns,
            market,
            &quotes,
            EntryContext {
                admission: &contradictory,
                ..context
            }
        )
        .is_err());
    contradictory = admission.clone();
    contradictory.activity_block = None;
    assert!(features
        .entry_frame(
            boundary,
            boundary.evaluated_at_ns,
            market,
            &quotes,
            EntryContext {
                admission: &contradictory,
                ..context
            }
        )
        .is_err());
    let mut other_scope = market.source_scope();
    let before_delayed_frame = crate::content_hash(snapshot).unwrap();
    let later_at = boundary.evaluated_at_ns + SECOND;
    assert!(features
        .entry_frame(boundary, later_at, market, &quotes, context)
        .is_err());
    assert!(features
        .entry_frame(
            boundary,
            boundary.evaluated_at_ns - 1,
            market,
            &quotes,
            context
        )
        .is_err());
    let mut later_quote = quote.clone();
    later_quote.key.sequence = 2;
    later_quote.sip.ns = later_at - 1;
    later_quote.available_at_ns = later_at;
    let mut later_quotes = crate::quote_state::Book::new(market.source_scope()).unwrap();
    later_quotes
        .bind_policy(empty_quote_policy(market.source_scope().provider))
        .unwrap();
    later_quotes.observe(&later_quote).unwrap();
    assert!(
        !features
            .entry_frame(boundary, later_at, market, &later_quotes, context)
            .unwrap()
            .fresh
    );
    assert_eq!(
        crate::content_hash(features.snapshot().unwrap().unwrap()).unwrap(),
        before_delayed_frame
    );
    other_scope.instrument = 2;
    let mut other_quote = quote.clone();
    other_quote.key.instrument = 2;
    let mut other_quotes = crate::quote_state::Book::new(other_scope).unwrap();
    other_quotes
        .bind_policy(empty_quote_policy(other_scope.provider))
        .unwrap();
    other_quotes.observe(&other_quote).unwrap();
    assert!(features
        .entry_frame(
            boundary,
            boundary.evaluated_at_ns,
            market,
            &other_quotes,
            context
        )
        .is_err());
    let mut stale = quote;
    stale.sip.ns -= SECOND;
    stale.available_at_ns -= SECOND;
    let mut stale_quotes = crate::quote_state::Book::new(market.source_scope()).unwrap();
    stale_quotes
        .bind_policy(empty_quote_policy(market.source_scope().provider))
        .unwrap();
    stale_quotes.observe(&stale).unwrap();
    assert!(features
        .entry_frame(
            boundary,
            boundary.evaluated_at_ns,
            market,
            &stale_quotes,
            context
        )
        .is_err());
    let swings = [crate::strategy_targets::Swing {
        id: "future".into(),
        lower: 9.,
        price: 10.,
        upper: 11.,
        pivot_at_ns: one.at_ns,
        confirmed_at_ns: one.at_ns + SECOND,
        support: true,
        active: true,
    }];
    assert!(features
        .entry_frame(
            boundary,
            boundary.evaluated_at_ns,
            market,
            &quotes,
            EntryContext {
                swings: &swings,
                ..context
            }
        )
        .is_err());
}
