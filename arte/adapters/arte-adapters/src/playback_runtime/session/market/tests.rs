use super::seed_catalog::{Entry as SeedEntry, RunSeedCatalog};
use super::*;
use arte_core::{
    event_order::Scope,
    market_structure::scheduler::playback::{sources::Shard, Frame, Limits, Mode, Poll},
    run_manifest::{Clock, Consumer, Execution, Manifest},
    strategy_dispatch,
    v7_extraction::Candle,
    v7_seed::{build, input_hash, SeedPolicy, SourceCertificate},
    v7_stream::StreamPolicy,
};
use std::collections::BTreeMap;
const S: u64 = 1_000_000_000;
pub(crate) fn fixture() -> (Document, Pinned, Catalog, Prepared, Bundle) {
    let bars: Vec<_> = (100..118)
        .map(|t| Candle {
            t,
            open: 10.,
            high: 11.,
            low: 9.,
            close: 10.,
            volume: 1.,
        })
        .collect();
    let seed = build(
        &bars,
        &[],
        SourceCertificate {
            instrument: 1,
            ticker: "TEST".into(),
            session: 20260914,
            start_second: 100,
            end_second: 120,
            source_generation: "offline".into(),
            input_hash: input_hash(&bars, &[]).unwrap(),
            certified_at_second: 121,
        },
        None,
        &SeedPolicy::default(),
        &SplitAdjustment::default(),
        121,
    )
    .unwrap();
    let seed = Bundle::from_seed(&seed).unwrap();
    let prepared = Prepared::new(
        Scope {
            provider: 1,
            instrument: 1,
            session: 20260915,
        },
        "historical-fixture",
        vec![Frame {
            watermark_ns: 300 * S,
            evaluated_at_ns: 300 * S + 1,
            inputs: vec![],
        }],
        Limits {
            maximum_frames: 1,
            maximum_events: 1,
            maximum_serialized_bytes: 10000,
        },
    )
    .unwrap();
    let sources = Catalog {
        schema_version: 1,
        authority_manifest_hash: "a".repeat(64),
        clock: Clock::Historical,
        shards: vec![Shard {
            provider: 1,
            instrument: 1,
            session: 20260915,
            prepared_hash: prepared.hash().into(),
            clock_model: "historical-fixture".into(),
        }],
    };
    let manifest = Manifest {
        schema_version: 3,
        run_id: "market-startup-test".into(),
        mode: strategy_dispatch::Mode::Backtest,
        code_release_hash: "a".repeat(64),
        source_manifest_hash: sources.hash().unwrap(),
        reference_manifest_hash: "b".repeat(64),
        seed_manifest_hash: content_hash(&seed.manifest).unwrap(),
        algorithm_manifest_hash: "c".repeat(64),
        dependency_plan_hash: "d".repeat(64),
        hardware_profile_hash: "e".repeat(64),
        clock: Clock::Historical,
        execution: Execution::Simulated {
            fill_model_hash: "f".repeat(64),
            cost_model_hash: "f".repeat(64),
        },
        consumers: vec![Consumer {
            account: "a".into(),
            instrument: 1,
            strategy_instance: "strategy".into(),
            strategy_kind: arte_core::strategy_dispatch::StrategyKind::GenericCandidate,
            execution_interval: arte_core::execution_interval::ExecutionInterval::Fixed(
                1_000_000_000,
            ),
            effective_config_hash: "f".repeat(64),
        }],
    };
    let manifest = Pinned::new(manifest.clone(), &manifest.hash().unwrap()).unwrap();
    let document = Document {
        schema_version: 1,
        manifest_hash: manifest.hash().into(),
        configuration: market_structure::Config {
            provider: 1,
            instrument: 1,
            session: 20260915,
            start_second: 200,
            end_second: 300,
            macd_periods: (2, 3, 2),
            maximum_bars: 100,
            maximum_market_events: 100,
            additional_timeframes: vec![],
            structure: StreamPolicy {
                input_generation: "offline".into(),
                ..StreamPolicy::default()
            },
        },
        split: SplitAdjustment::default(),
        quote_policy: Policy {
            provider: 1,
            valid_from_ns: 200 * S,
            valid_to_ns: 300 * S,
            available_at_ns: 199 * S,
            source_manifest_hash: "a".repeat(64),
            allowed_conditions: Default::default(),
            allowed_indicators: Default::default(),
            allow_empty_conditions: true,
            allow_empty_indicators: true,
        },
        maximum_pending_events: 100,
        frames_per_poll: 1,
        maximum_consumers: 1,
    };
    (document, manifest, sources, prepared, seed)
}
#[test]
fn portable_market_inputs_build_a_paused_run_and_empty_interval_completes() {
    let (document, manifest, sources, prepared, seed) = fixture();
    let hash = document.hash().unwrap();
    let bytes = serde_json::to_vec(&document).unwrap();
    let decoded = Document::read(bytes.as_slice(), &hash).unwrap();
    let mut run = decoded
        .assemble(&hash, &manifest, &sources, prepared, &seed)
        .unwrap();
    assert_eq!(run.status().mode, Mode::Paused);
    assert_eq!(run.status().admitted_events, 0);
    assert_eq!(run.scopes().len(), 1);
    assert_eq!(run.market().unwrap().source_scope().instrument, 1);
    assert_eq!(
        run.quotes().unwrap().policy_hash().unwrap(),
        content_hash(&document.quote_policy).unwrap()
    );
    run.resume().unwrap();
    for _ in 0..3 {
        if run.poll().unwrap() == Poll::Complete {
            break;
        }
    }
    assert_eq!(run.status().mode, Mode::Complete);
}
#[test]
fn combined_run_resolves_each_historical_seed_without_cross_ticker_substitution() {
    use crate::playback_runtime::session::{multi, Limits as SessionLimits};
    use arte_core::{
        events::{Decimal, EventKey, EventKind, Observation, Payload, SourceTime},
        execution_interval::ExecutionInterval,
        market_structure::scheduler::playback::Input,
        orders::{Bracket, Side},
        portfolio::{Account, FundingStatus, Reservation},
        simulation_costs,
        strategy350_effective::Config as Effective,
        strategy350_gap,
        strategy_dispatch::StrategyKind,
    };
    let (mut first_doc, single, mut sources, _, first_seed) = fixture();
    let prepared_trade = |instrument: u64, sip_ns: u64| {
        let trade = Input {
            observation: Observation {
                key: EventKey {
                    provider: 1,
                    instrument,
                    session: 20260915,
                    kind: EventKind::Trade,
                    sequence: 1,
                },
                payload: Payload::Trade {
                    price: Decimal {
                        atoms: 10,
                        scale: 0,
                    },
                    size: Decimal { atoms: 1, scale: 0 },
                    exchange: 1,
                    trade_id: format!("{instrument}-1"),
                    trf: None,
                    conditions: vec![],
                    correction: None,
                },
                sip: SourceTime {
                    ns: sip_ns,
                    precision_ns: 1,
                },
                participant: None,
                available_at_ns: sip_ns,
                receipt: None,
            },
            eligible: true,
        };
        let mut inputs = vec![trade];
        if instrument == 2 {
            let quote = Input {
                observation: Observation {
                    key: EventKey {
                        provider: 1,
                        instrument,
                        session: 20260915,
                        kind: EventKind::Quote,
                        sequence: 0,
                    },
                    payload: Payload::Quote {
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
                    },
                    sip: SourceTime {
                        ns: sip_ns,
                        precision_ns: 1,
                    },
                    participant: None,
                    available_at_ns: sip_ns,
                    receipt: None,
                },
                eligible: false,
            };
            inputs.insert(0, quote);
        }
        Prepared::new(
            Scope {
                provider: 1,
                instrument,
                session: 20260915,
            },
            "historical-fixture",
            vec![
                Frame {
                    watermark_ns: sip_ns + S,
                    evaluated_at_ns: sip_ns + S,
                    inputs,
                },
                Frame {
                    watermark_ns: 300 * S,
                    evaluated_at_ns: 300 * S + 1,
                    inputs: vec![],
                },
            ],
            Limits {
                maximum_frames: 2,
                maximum_events: 2,
                maximum_serialized_bytes: 10_000,
            },
        )
        .unwrap()
    };
    let first_prepared = prepared_trade(1, 200 * S);
    sources.shards[0].prepared_hash = first_prepared.hash().into();
    let bars: Vec<_> = (100..118)
        .map(|t| Candle {
            t,
            open: 10.,
            high: 11.,
            low: 9.,
            close: 10.,
            volume: 1.,
        })
        .collect();
    let second_seed = build(
        &bars,
        &[],
        SourceCertificate {
            instrument: 2,
            ticker: "OTHER".into(),
            session: 20260914,
            start_second: 100,
            end_second: 120,
            source_generation: "offline".into(),
            input_hash: input_hash(&bars, &[]).unwrap(),
            certified_at_second: 121,
        },
        None,
        &SeedPolicy::default(),
        &SplitAdjustment::default(),
        121,
    )
    .unwrap();
    let second_seed = Bundle::from_seed(&second_seed).unwrap();
    let second_prepared = prepared_trade(2, 202 * S);
    sources.shards.push(Shard {
        provider: 1,
        instrument: 2,
        session: 20260915,
        prepared_hash: second_prepared.hash().into(),
        clock_model: "historical-fixture".into(),
    });
    let seeds = RunSeedCatalog {
        schema_version: 1,
        entries: vec![
            SeedEntry {
                provider: 1,
                instrument: 1,
                session: 20260915,
                seed_manifest_hash: content_hash(&first_seed.manifest).unwrap(),
            },
            SeedEntry {
                provider: 1,
                instrument: 2,
                session: 20260915,
                seed_manifest_hash: content_hash(&second_seed.manifest).unwrap(),
            },
        ],
    };
    let mut combined = single.manifest().clone();
    combined.source_manifest_hash = sources.hash().unwrap();
    combined.seed_manifest_hash = seeds.hash().unwrap();
    let effective = Effective {
        execution_interval: ExecutionInterval::Fixed(S),
        gap: strategy350_gap::Config {
            execution_interval: ExecutionInterval::Fixed(100_000_000),
            maximum_levels: 1_000,
        },
        signal_config_hash: "a".repeat(64),
        screen_config_hash: "b".repeat(64),
        price_gate_config_hash: "c".repeat(64),
        macd_config_hash: "d".repeat(64),
        noise_config_hash: "e".repeat(64),
        bos_config_hash: "f".repeat(64),
        level_book_config_hash: "1".repeat(64),
        rule_set_hash: "2".repeat(64),
        account_risk_hash: "3".repeat(64),
        watchlist_config_hash: None,
    };
    let cost_model = simulation_costs::Model {
        schema_version: 1,
        currency: "USD".into(),
        currency_scale: 2,
        fixed_per_fill_minor: 0,
        per_share_atoms: 0,
        per_share_scale: 0,
        minimum_per_fill_minor: 0,
    };
    combined.execution = Execution::Simulated {
        fill_model_hash: crate::test_fill_model().hash().unwrap(),
        cost_model_hash: cost_model.hash().unwrap(),
    };
    combined.consumers[0].strategy_kind = StrategyKind::Strategy350;
    combined.consumers[0].effective_config_hash = effective.hash().unwrap();
    let mut other = combined.consumers[0].clone();
    other.instrument = 2;
    other.account = "b".into();
    combined.consumers.push(other);
    let combined_hash = combined.hash().unwrap();
    let combined = Pinned::new(combined, &combined_hash).unwrap();
    first_doc.manifest_hash = combined.hash().into();
    let mut second_doc = first_doc.clone();
    second_doc.configuration.instrument = 2;
    let first_hash = first_doc.hash().unwrap();
    let second_hash = second_doc.hash().unwrap();
    let first_run = first_doc
        .clone()
        .assemble_multi(
            &first_hash,
            &combined,
            &sources,
            first_prepared.clone(),
            &first_seed,
            &seeds,
        )
        .unwrap();
    let second_run = second_doc
        .clone()
        .assemble_multi(
            &second_hash,
            &combined,
            &sources,
            second_prepared.clone(),
            &second_seed,
            &seeds,
        )
        .unwrap();
    assert_eq!(first_run.market_scope().instrument, 1);
    assert_eq!(second_run.market_scope().instrument, 2);
    assert!(first_doc
        .clone()
        .assemble_multi(
            &first_hash,
            &combined,
            &sources,
            first_prepared.clone(),
            &second_seed,
            &seeds,
        )
        .is_err());
    let mut missing = seeds.clone();
    missing.entries.pop();
    assert!(first_doc
        .assemble_multi(
            &first_hash,
            &combined,
            &sources,
            first_prepared,
            &first_seed,
            &missing,
        )
        .is_err());
    assert!(second_doc
        .assemble_multi(
            &second_hash,
            &combined,
            &sources,
            second_prepared,
            &first_seed,
            &seeds,
        )
        .is_err());
    let mut configs = BTreeMap::new();
    let mut states = BTreeMap::new();
    for (account, instrument) in [("a", 1), ("b", 2)] {
        let scope = combined.scope(account, instrument, "strategy").unwrap();
        let key = content_hash(&scope).unwrap();
        configs.insert(key.clone(), effective.clone());
        states.insert(key, instrument);
    }
    let accounts = ["a", "b"]
        .into_iter()
        .map(|id| {
            (
                id.into(),
                Account {
                    currency: "USD".into(),
                    currency_scale: 2,
                    simulation_run_id: Some(combined.manifest().run_id.clone()),
                    budget_minor: if id == "a" { 10_000 } else { 20_000 },
                    broker_available_minor: if id == "a" { 10_000 } else { 20_000 },
                    balance_at_ns: 199 * S,
                    max_balance_age_ns: 2 * S,
                    reservations: BTreeMap::new(),
                },
            )
        })
        .collect();
    let request = multi::Request {
        manifest: &combined,
        sources: &sources,
        markets: vec![
            multi::MarketRun {
                run: second_run,
                price_scale: 2,
            },
            multi::MarketRun {
                run: first_run,
                price_scale: 2,
            },
        ],
        configurations: configs,
        initial_states: states,
        accounts,
        fill_model: crate::test_fill_model(),
        cost_model,
        limits: SessionLimits {
            maximum_orders: 10,
            maximum_positions: 2,
            maximum_fills: 10,
            maximum_lots_per_position: 4,
            maximum_pending_fills: 100,
            maximum_candidate_state_bytes: 1024,
        },
    };
    let startup_hash = request.hash().unwrap();
    let mut session = multi::Session::from_request(request, &startup_hash).unwrap();
    assert_eq!(session.startup_hash(), startup_hash);
    assert_eq!(session.controller.shard_count(), 2);
    assert_eq!(session.strategy.len(), 2);
    session
        .controller
        .require_complete_portfolio(&mut session.portfolio, &combined, &BTreeMap::new())
        .unwrap();
    session
        .portfolio
        .reserve(
            "a",
            Reservation {
                command_id: "reserved-a".into(),
                instrument: 1,
                cash_minor: 1_000,
            },
            200 * S,
        )
        .unwrap();
    assert!(matches!(
        session.portfolio.funding_status("a", "reserved-a").unwrap(),
        FundingStatus::Reserved(_)
    ));
    assert_eq!(
        session.portfolio.funding_status("b", "reserved-a").unwrap(),
        FundingStatus::Absent
    );
    assert!(session
        .controller
        .require_complete_portfolio(&mut session.portfolio, &combined, &BTreeMap::new())
        .is_err());
    session
        .controller
        .seed_test_order(
            1,
            Bracket {
                command_id: "later-shard-fill".into(),
                account: "b".into(),
                instrument: 2,
                side: Side::Long,
                quantity: 1,
                entry: 1001,
                price_scale: 2,
                stop: Some(900),
                target: Some(1100),
                tick: 1,
                deadline_ns: 205 * S,
            },
            200 * S,
        )
        .unwrap();
    session.controller.resume_all().unwrap();
    let mut selected = None;
    for _ in 0..100 {
        let polled = session.controller.poll().unwrap();
        if !matches!(polled, crate::playback_runtime::multi::MultiPoll::Yield) {
            selected = Some(polled);
            break;
        }
    }
    assert!(
        matches!(
            selected,
            Some(crate::playback_runtime::multi::MultiPoll::Boundary { shard: 0 })
        ),
        "{selected:?}"
    );
    assert_eq!(session.controller.selected().unwrap().unwrap().0, 0);
    assert!(session.controller.controllers()[1].decision_view().is_err());
    assert_eq!(
        session.controller.controllers()[1]
            .execution_status()
            .pending_fills,
        0
    );
    let selected_controller = &session.controller.controllers()[0];
    let image = session.strategy[&1]
        .checkpoint(selected_controller, 100_000)
        .unwrap();
    let scope = combined.scope("a", 1, "strategy").unwrap();
    let scope_hash = content_hash(&scope).unwrap();
    let configs = BTreeMap::from([(scope_hash.clone(), effective)]);
    let readbacks = BTreeMap::from([(scope_hash.clone(), Vec::new())]);
    let restored =
        crate::playback_runtime::strategy350_accounts::Accounts::<u64>::restore_checkpoint(
            &image,
            &image.root.id,
            selected_controller,
            configs.clone(),
            &readbacks,
            1024,
            100_000,
        )
        .unwrap();
    assert_eq!(*restored.state(&scope_hash).unwrap(), 1);
    assert!(
        crate::playback_runtime::strategy350_accounts::Accounts::<u64>::restore_checkpoint(
            &image,
            &"0".repeat(64),
            selected_controller,
            configs,
            &readbacks,
            1024,
            100_000,
        )
        .is_err()
    );
}
#[test]
fn wrong_identity_future_evidence_and_partial_source_are_rejected() {
    for fault in 0..11 {
        let (mut document, manifest, sources, prepared, mut seed) = fixture();
        let original_hash = document.hash().unwrap();
        match fault {
            0 => document.manifest_hash = "0".repeat(64),
            1 => document.quote_policy.provider = 2,
            2 => document.quote_policy.available_at_ns = 200 * S + 1,
            3 => document.quote_policy.valid_to_ns -= 1,
            4 => document.configuration.end_second += 1,
            5 => document.configuration.session += 1,
            6 => document.maximum_pending_events = 0,
            7 => {
                seed.objects.pop_first();
            }
            8 => seed.manifest.source_generation.push('x'),
            9 => document.configuration.start_second = 100, // seed unavailable
            10 => document.configuration.maximum_bars += 1, // old independent pin
            _ => unreachable!(),
        }
        let hash = if fault == 10 {
            original_hash
        } else {
            document.hash().unwrap_or_default()
        };
        assert!(
            document
                .assemble(&hash, &manifest, &sources, prepared, &seed)
                .is_err(),
            "fault {fault}"
        );
    }
}
#[test]
fn portable_document_rejects_unknown_fields_oversize_and_wrong_pin() {
    let (document, _, _, _, _) = fixture();
    let hash = document.hash().unwrap();
    let bytes = serde_json::to_vec(&document).unwrap();
    assert!(Document::read(bytes.as_slice(), &"0".repeat(64)).is_err());
    let mut json = serde_json::to_value(&document).unwrap();
    json["configuration"]["unknown"] = true.into();
    assert!(Document::read(serde_json::to_vec(&json).unwrap().as_slice(), &hash).is_err());
    assert!(Document::read(std::io::repeat(b' '), &hash).is_err());
}
