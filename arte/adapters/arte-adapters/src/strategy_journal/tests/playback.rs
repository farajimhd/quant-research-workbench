use super::*;
mod lifecycle;
mod policies;
use arte_core::{
    market_structure::scheduler::{
        playback::{
            accounts::Run,
            sources::{Catalog, Shard},
            Frame, Input, Limits, Poll, Prepared as InputData,
        },
        Scheduler,
    },
    market_structure::{Config, Ordered, Runtime as Market},
    run_manifest::{Clock, Consumer, Execution, Manifest, Pinned},
    v7_extraction::Candle,
    v7_seed::{build, input_hash, SeedPolicy, SourceCertificate, SplitAdjustment},
    v7_stream::StreamPolicy,
};

#[test]
fn playback_rejects_cost_binding_from_different_source_manifest() {
    use arte_core::{execution_positions::Projection, simulated_execution::Simulator};
    let run = run_with_quote(true);
    let costs = run_with_costs(false, false, false).1;
    let mut execution = crate::simulation_runtime::Runtime::new(
        Simulator::new_scoped("run", 1, 2, 2, 10000).unwrap(),
        Projection::new(2, 10, 4).unwrap(),
        4,
    )
    .unwrap();
    execution
        .bind_source(run.market().unwrap().source_scope())
        .unwrap();
    assert!(
        crate::playback_runtime::Runtime::new(run, execution, crate::test_fill_model(), costs)
            .is_err()
    );
}

#[tokio::test]
async fn released_playback_quote_drives_one_fill_with_retryable_journal() {
    use arte_core::{execution_positions::Projection, simulated_execution::Simulator};
    let (run, costs) = run_with_costs(true, false, false);
    let source = run.market().unwrap().source_scope();
    let simulator = Simulator::new_scoped("run", 1, 2, 2, 10000).unwrap();
    let test_order = arte_core::orders::Bracket {
        command_id: "order".into(),
        account: "a".into(),
        instrument: 1,
        side: arte_core::orders::Side::Long,
        quantity: 1,
        entry: 1001,
        price_scale: 2,
        stop: Some(900),
        target: Some(1100),
        tick: 1,
        deadline_ns: 202_000_000_000,
    };
    let mut execution =
        crate::simulation_runtime::Runtime::new(simulator, Projection::new(2, 10, 4).unwrap(), 4)
            .unwrap();
    execution.bind_source(source).unwrap();
    assert!(execution.quote_playback(&run, 2_000_000_000).is_err());
    let mut foreign = crate::simulation_runtime::Runtime::new(
        Simulator::new_scoped("foreign", 1, 2, 2, 10000).unwrap(),
        Projection::new(2, 10, 4).unwrap(),
        4,
    )
    .unwrap();
    foreign.bind_source(source).unwrap();
    assert!(crate::playback_runtime::Runtime::new(
        run_with_quote(true),
        foreign,
        crate::test_fill_model(),
        run_with_costs(true, false, false).1
    )
    .is_err());
    let mut controller =
        crate::playback_runtime::Runtime::new(run, execution, crate::test_fill_model(), costs)
            .unwrap();
    controller
        .seed_test_order(test_order, 200_000_000_000)
        .unwrap();
    controller.resume().unwrap();
    assert_eq!(controller.poll().unwrap(), Poll::Boundary);
    assert_eq!(controller.poll().unwrap(), Poll::Boundary);
    assert_eq!(controller.execution_status().pending_fills, 1);
    assert!(controller.decision_view().is_err());
    assert!(controller.acknowledge().is_err());
    struct FillStore {
        fail: bool,
        calls: usize,
    }
    impl crate::fill_journal::Publisher for FillStore {
        async fn publish(
            &mut self,
            batch: &crate::fill_journal::Batch,
        ) -> Result<std::collections::BTreeMap<String, String>> {
            self.calls += 1;
            if self.fail {
                self.fail = false;
                return Err(Error::Unready("injected journal failure".into()));
            }
            Ok(batch.rows().clone())
        }
    }
    let mut store = FillStore {
        fail: true,
        calls: 0,
    };
    assert!(controller.commit_fills(&mut store).await.is_err());
    assert_eq!(controller.fees_minor("order").unwrap(), None);
    assert!(controller.decision_view().is_err());
    assert_eq!(controller.execution_status().pending_fills, 1);
    assert!(controller.commit_fills(&mut store).await.unwrap());
    assert_eq!(controller.execution_status().pending_fills, 0);
    assert_eq!(controller.poll().unwrap(), Poll::Boundary);
    assert_eq!(controller.execution_status().pending_fills, 0);
    assert_eq!(store.calls, 2);
    assert_eq!(controller.fees_minor("order").unwrap(), Some(2));
    assert!(controller.acknowledge().is_err()); // Account decisions still missing.
    let view = controller.decision_view().unwrap();
    let input = view.pending().unwrap().unwrap().input("features".into());
    let mut runtimes: Vec<_> = view
        .scopes()
        .iter()
        .map(|scope| {
            let mut runtime =
                arte_core::strategy_transaction::Runtime::new(scope.clone(), 0_u64, 1024).unwrap();
            let template = prepared_account(&scope.account);
            let mut safety = template.pending_decision().unwrap().safety.clone();
            safety.position_quantity = if scope.account == "a" { 1 } else { 0 };
            runtime
                .prepare(input.clone(), &safety, "evidence".into(), |_| {
                    Ok(vec![Action::Hold {
                        reason: "quote-observed".into(),
                    }])
                })
                .unwrap();
            runtime
        })
        .collect();
    let mut stores = [timed(0, false), timed(0, false)];
    let mut writes: Vec<_> = runtimes
        .iter_mut()
        .zip(stores.iter_mut())
        .map(|(r, p)| accounts::Write::new(r, p))
        .collect();
    assert!(accounts::commit_accounts(&mut writes, &mut controller, 2)
        .await
        .unwrap()
        .iter()
        .all(|o| o.result.is_ok()));
    controller.acknowledge().unwrap();
    assert_eq!(controller.status().acknowledged_boundaries, 1);
    let key = arte_core::execution_positions::Key {
        origin_hash: arte_core::content_hash(&(
            "simulated-position-v1",
            "run",
            arte_core::simulated_execution::MODEL,
        ))
        .unwrap(),
        account: "a".into(),
        instrument: 1,
    };
    assert_eq!(controller.position(&key).unwrap().quantity, 1);
}

fn run_with_quote(include_quote: bool) -> Run {
    run_fixture(include_quote, false)
}
fn run_fixture(include_quote: bool, lifecycle: bool) -> Run {
    run_data(include_quote, lifecycle, false)
}
fn run_data(include_quote: bool, lifecycle: bool, target_exit: bool) -> Run {
    run_with_costs(include_quote, lifecycle, target_exit).0
}
fn run_with_costs(
    include_quote: bool,
    lifecycle: bool,
    target_exit: bool,
) -> (Run, arte_core::simulation_costs::Pinned) {
    let (run, costs, _, _) = run_recovery_fixture(include_quote, lifecycle, target_exit);
    (run, costs)
}
struct RecoveryInput {
    prepared: InputData,
    catalog: Catalog,
    seed_hash: String,
    configuration_hash: String,
}
fn run_recovery_fixture(
    include_quote: bool,
    lifecycle: bool,
    target_exit: bool,
) -> (
    Run,
    arte_core::simulation_costs::Pinned,
    Pinned,
    RecoveryInput,
) {
    run_candidate_fixture(include_quote, lifecycle, target_exit, false)
}
fn run_candidate_fixture(
    include_quote: bool,
    lifecycle: bool,
    target_exit: bool,
    features: bool,
) -> (
    Run,
    arte_core::simulation_costs::Pinned,
    Pinned,
    RecoveryInput,
) {
    const S: u64 = 1_000_000_000;
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
    let market = Market::new(
        &seed,
        Config {
            provider: 1,
            instrument: 1,
            session: 20260915,
            start_second: 200,
            end_second: 300,
            macd_periods: (2, 3, 2),
            maximum_bars: 100,
            maximum_market_events: 100,
            additional_timeframes: if features {
                vec![arte_core::market_structure::Timeframe {
                    interval_ns: 5 * S,
                    macd_periods: (12, 26, 9),
                    maximum_bars: 100,
                }]
            } else {
                vec![]
            },
            structure: StreamPolicy {
                input_generation: "offline".into(),
                ..StreamPolicy::default()
            },
        },
        &SplitAdjustment::default(),
    )
    .unwrap();
    let configuration_hash = market.configuration_hash().to_owned();
    let mut scheduler = Scheduler::new(Ordered::new(market, 10).unwrap(), "run".into()).unwrap();
    scheduler
        .bind_quote_policy(std::sync::Arc::new(crate::test_quote_policy()))
        .unwrap();
    use arte_core::events::*;
    let prepared = InputData::new(
        scheduler.scope(),
        "historical-test",
        vec![Frame {
            watermark_ns: 201 * S,
            evaluated_at_ns: 201 * S,
            inputs: {
                let mut inputs = vec![Input {
                    eligible: true,
                    observation: Observation {
                        key: EventKey {
                            provider: 1,
                            instrument: 1,
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
                            trade_id: "1".into(),
                            trf: None,
                            conditions: vec![],
                            correction: None,
                        },
                        sip: SourceTime {
                            ns: 200 * S,
                            precision_ns: 1,
                        },
                        participant: None,
                        available_at_ns: 200 * S,
                        receipt: None,
                    },
                }];
                if include_quote {
                    let mut quote = inputs[0].observation.clone();
                    quote.key.kind = EventKind::Quote;
                    quote.key.sequence = 0;
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
                    inputs.insert(
                        0,
                        Input {
                            observation: quote,
                            eligible: false,
                        },
                    );
                }
                if lifecycle {
                    for sequence in [2, 3, 4] {
                        let mut quote = inputs[0].clone();
                        quote.observation.key.sequence = sequence;
                        quote.observation.sip.ns += sequence;
                        quote.observation.available_at_ns += sequence;
                        if target_exit && sequence >= 3 {
                            if let Payload::Quote { bid, ask, .. } = &mut quote.observation.payload
                            {
                                bid.atoms = if sequence == 3 { 1120 } else { 1150 };
                                ask.atoms = bid.atoms + 2;
                            }
                        }
                        inputs.push(quote);
                    }
                }
                inputs
            },
        }],
        Limits {
            maximum_frames: 1,
            maximum_events: 5,
            maximum_serialized_bytes: 10000,
        },
    )
    .unwrap();
    let catalog = Catalog {
        schema_version: 1,
        authority_manifest_hash: "a".repeat(64),
        clock: Clock::Historical,
        shards: vec![Shard {
            provider: 1,
            instrument: 1,
            session: 20260915,
            prepared_hash: prepared.hash().into(),
            clock_model: "historical-test".into(),
        }],
    };
    let cost_model = arte_core::simulation_costs::Model {
        schema_version: 1,
        currency: "USD".into(),
        currency_scale: 2,
        fixed_per_fill_minor: 1,
        per_share_atoms: 5,
        per_share_scale: 3,
        minimum_per_fill_minor: 2,
    };
    let m = Manifest {
        schema_version: 1,
        run_id: "run".into(),
        mode: Mode::Backtest,
        code_release_hash: "a".repeat(64),
        source_manifest_hash: catalog.hash().unwrap(),
        reference_manifest_hash: "b".repeat(64),
        seed_manifest_hash: "c".repeat(64),
        algorithm_manifest_hash: "d".repeat(64),
        dependency_plan_hash: "e".repeat(64),
        hardware_profile_hash: "f".repeat(64),
        clock: Clock::Historical,
        execution: Execution::Simulated {
            fill_model_hash: crate::test_fill_model().hash().unwrap(),
            cost_model_hash: cost_model.hash().unwrap(),
        },
        consumers: ["a", "b"]
            .into_iter()
            .map(|account| Consumer {
                account: account.into(),
                instrument: 1,
                strategy_instance: "s".into(),
                effective_config_hash: if features {
                    let config = policies::config(account);
                    let state = arte_core::candidate_features::State::new(
                        scheduler.state().unwrap(),
                        config.features.clone(),
                    )
                    .unwrap();
                    config
                        .effective_hash(&state, scheduler.quotes().unwrap().policy_hash().unwrap())
                        .unwrap()
                } else {
                    "3".repeat(64)
                },
            })
            .collect(),
    };
    let hash = m.hash().unwrap();
    let manifest = Pinned::new(m, &hash).unwrap();
    let costs = arte_core::simulation_costs::Pinned::new(cost_model, &manifest).unwrap();
    let run = Run::new(&manifest, &catalog, scheduler, prepared.clone(), 1, 2).unwrap();
    (
        run,
        costs,
        manifest,
        RecoveryInput {
            prepared,
            catalog,
            seed_hash: seed.hash,
            configuration_hash,
        },
    )
}

#[tokio::test]
async fn candidate_owner_preflights_consumers_before_journal_io() {
    candidate_owner_retry(false).await;
}
#[tokio::test(start_paused = true)]
async fn candidate_owner_retains_progress_when_journal_acknowledgment_is_cancelled() {
    candidate_owner_retry(true).await;
}
async fn candidate_owner_retry(cancel: bool) {
    use crate::playback_runtime::{candidates::Candidates, Runtime as Controller};
    use arte_core::{
        candidate_features::Config as Features, execution_positions::Projection,
        simulated_execution::Simulator,
    };
    let (run, costs, manifest, _) = run_candidate_fixture(true, false, false, true);
    let mut execution = crate::simulation_runtime::Runtime::new(
        Simulator::new_scoped("run", 1, 2, 2, 10000).unwrap(),
        Projection::new(2, 10, 4).unwrap(),
        4,
    )
    .unwrap();
    execution
        .bind_source(run.market().unwrap().source_scope())
        .unwrap();
    let mut controller = Controller::new(run, execution, crate::test_fill_model(), costs).unwrap();
    let config = Features {
        setup: arte_core::strategy_setup::SetupSettings {
            range_ns: 30_000_000_000,
            minimum_bars: 1,
            maximum_gap_ns: 0,
        },
        forming_macd: true,
        minimum_range_pct: 0.,
        minimum_progress_pct: 0.,
        maximum_quote_age_ns: 2_000_000_000,
        maximum_completed_bar_age_ns: 2_000_000_000,
        maximum_levels: 100,
    };
    let configurations: std::collections::BTreeMap<_, _> = manifest
        .manifest()
        .consumers
        .iter()
        .map(|c| {
            let scope = manifest
                .scope(&c.account, c.instrument, &c.strategy_instance)
                .unwrap();
            (
                arte_core::content_hash(&scope).unwrap(),
                policies::config(&c.account),
            )
        })
        .collect();
    let mut wrong = configurations.clone();
    wrong.pop_first();
    assert!(Candidates::configured(&controller, &manifest, wrong, 100_000).is_err());
    let mut wrong = configurations.clone();
    wrong
        .values_mut()
        .next()
        .unwrap()
        .position
        .failure_buffer_ticks += 1.;
    assert!(Candidates::configured(&controller, &manifest, wrong, 100_000).is_err());
    let mut wrong = configurations.clone();
    wrong.insert("foreign".into(), policies::config("a"));
    assert!(Candidates::configured(&controller, &manifest, wrong, 100_000).is_err());
    use arte_core::candidate_config::document::{Consumer, Document};
    let document = Document {
        schema_version: 1,
        manifest_hash: manifest.hash().into(),
        consumers: manifest
            .manifest()
            .consumers
            .iter()
            .map(|c| Consumer {
                account: c.account.clone(),
                instrument: c.instrument,
                strategy_instance: c.strategy_instance.clone(),
                config: policies::config(&c.account),
            })
            .collect(),
    };
    let bytes = serde_json::to_vec(&document).unwrap();
    let mut wrong = document.clone();
    wrong.consumers[1] = wrong.consumers[0].clone();
    assert!(wrong.bind(&manifest).is_err());
    let mut wrong = document.clone();
    wrong.manifest_hash = "a".repeat(64);
    assert!(wrong.bind(&manifest).is_err());
    let mut wrong = document.clone();
    wrong.consumers[0].account = "foreign".into();
    assert!(wrong.bind(&manifest).is_err());
    let mut wrong = serde_json::to_value(&document).unwrap();
    wrong["consumers"][0]["config"]["entry"]["maximum_chase_typo"] = 1.into();
    assert!(Document::decode(&serde_json::to_vec(&wrong).unwrap()).is_err());
    assert!(Document::decode(b"{}").is_err());
    assert!(
        Candidates::from_reader(&controller, &manifest, &bytes[..bytes.len() - 1], 100_000)
            .is_err()
    );
    assert!(
        Candidates::from_reader(&controller, &manifest, std::io::repeat(b' '), 100_000).is_err()
    );
    let mut candidates =
        Candidates::from_reader(&controller, &manifest, bytes.as_slice(), 100_000).unwrap();
    assert_eq!(candidates.scope_hashes().count(), 2);
    assert!(candidates.state("unknown").is_err());
    assert!(candidates.observe(&controller).is_err());
    controller.resume().unwrap();
    assert_eq!(controller.poll().unwrap(), Poll::Boundary);
    assert!(Candidates::new(&controller, &manifest, config, 100_000).is_err());
    assert!(candidates.observe(&controller).unwrap());
    assert!(!candidates.observe(&controller).unwrap());
    struct Never;
    impl Publisher for Never {
        async fn append(&mut self, _: &Batch) -> Result<Vec<Record>> {
            panic!("preflight must prevent I/O")
        }
    }
    let mut publishers: std::collections::BTreeMap<_, _> = candidates
        .scope_hashes()
        .map(|id| (id.to_owned(), Never))
        .collect();
    assert!(candidates
        .commit_accounts(&mut controller, &mut publishers, 0)
        .await
        .is_err());
    assert!(candidates
        .commit_accounts(&mut controller, &mut publishers, 2)
        .await
        .is_err());
    publishers.insert("foreign".into(), Never);
    assert!(candidates
        .commit_accounts(&mut controller, &mut publishers, 2)
        .await
        .is_err());
    assert!(controller.acknowledge().is_err());
    let scopes = controller.decision_view().unwrap().scopes().to_vec();
    let at_ns = controller
        .decision_view()
        .unwrap()
        .pending()
        .unwrap()
        .unwrap()
        .evaluated_at_ns;
    for scope in &scopes {
        let template = prepared_account(&scope.account);
        let mut safety = template.pending_decision().unwrap().safety.clone();
        safety.pending_entry = scope.account == "b";
        safety.flatten = scope.account == "b";
        let broker = arte_core::strategy_candidate::PositionObservation {
            revision: 1,
            at_ns,
            quantity: 0,
            average_price: None,
            stop: None,
            target: None,
            pending_entry: safety.pending_entry,
        };
        let decision = candidates
            .prepare_observation(
                &controller,
                &arte_core::content_hash(scope).unwrap(),
                &safety,
                &broker,
            )
            .unwrap();
        if scope.account == "b" {
            assert!(matches!(
                decision.actions.as_slice(),
                [Action::CancelEntry { .. }]
            ));
        } else {
            assert!(matches!(decision.actions.as_slice(), [Action::Wait { .. }]));
        }
    }
    struct RetryStore {
        calls: usize,
        fail: bool,
        delay: bool,
        stored: Vec<Record>,
    }
    impl Publisher for RetryStore {
        async fn append(&mut self, batch: &Batch) -> Result<Vec<Record>> {
            self.calls += 1;
            if self.fail {
                self.fail = false;
                return Err(Error::Unready("injected account failure".into()));
            }
            if self.stored.is_empty() {
                self.stored = batch.records().to_vec();
            } else {
                batch.verify_readback(&self.stored)?;
            }
            if self.delay {
                self.delay = false;
                // Persisted in the in-memory journal, but readback has not arrived.
                tokio::time::sleep(std::time::Duration::from_secs(60)).await;
            }
            Ok(self.stored.clone())
        }
    }
    let keys: Vec<_> = candidates.scope_hashes().map(str::to_owned).collect();
    let mut stores = keys
        .iter()
        .enumerate()
        .map(|(i, key)| {
            (
                key.clone(),
                RetryStore {
                    calls: 0,
                    fail: i == 1 && !cancel,
                    delay: i == 1 && cancel,
                    stored: vec![],
                },
            )
        })
        .collect();
    if cancel {
        assert!(tokio::time::timeout(
            std::time::Duration::from_millis(1),
            candidates.commit_accounts(&mut controller, &mut stores, 2)
        )
        .await
        .is_err());
        assert_eq!(stores[&keys[0]].stored.len(), 1);
        assert_eq!(stores[&keys[1]].stored.len(), 1);
        assert_eq!(controller.decision_view().unwrap().remaining(), Some(1));
    } else {
        let outcomes = candidates
            .commit_accounts(&mut controller, &mut stores, 2)
            .await
            .unwrap();
        assert_eq!(outcomes.iter().filter(|o| o.result.is_ok()).count(), 1);
        assert_eq!(outcomes.iter().filter(|o| o.result.is_err()).count(), 1);
    }
    assert!(controller.acknowledge().is_err());
    let outcomes = candidates
        .commit_accounts(&mut controller, &mut stores, 2)
        .await
        .unwrap();
    assert_eq!(outcomes.len(), 1);
    assert!(outcomes[0].result.is_ok());
    assert_eq!(stores[&keys[0]].calls, 1);
    assert_eq!(stores[&keys[1]].calls, 2);
    assert_eq!(stores[&keys[0]].stored.len(), 1);
    assert_eq!(stores[&keys[1]].stored.len(), 1);
    assert!(candidates
        .commit_accounts(&mut controller, &mut stores, 2)
        .await
        .unwrap()
        .is_empty());
    assert!(controller.acknowledge().is_err());
    let pending = controller.pending_actions();
    assert_eq!(pending.len(), 1);
    controller
        .cancel_entry_action(&pending[0].decision_id, pending[0].action_index)
        .unwrap();
    controller.acknowledge().unwrap();
    assert_eq!(controller.poll().unwrap(), Poll::Boundary);
    candidates.observe(&controller).unwrap();
    let scope = &scopes[0];
    let template = prepared_account(&scope.account);
    let broker = arte_core::strategy_candidate::PositionObservation {
        revision: 1,
        at_ns,
        quantity: 0,
        average_price: None,
        stop: None,
        target: None,
        pending_entry: false,
    };
    assert!(candidates
        .prepare_observation(
            &controller,
            &arte_core::content_hash(scope).unwrap(),
            &template.pending_decision().unwrap().safety,
            &broker
        )
        .is_err());
    for scope in &scopes {
        let mut broker = broker.clone();
        broker.revision = 2;
        let safety = prepared_account(&scope.account)
            .pending_decision()
            .unwrap()
            .safety
            .clone();
        let decision = candidates
            .prepare_configured_intrabar(
                &controller,
                &arte_core::content_hash(scope).unwrap(),
                arte_core::candidate_features::AcquisitionContext {
                    tradable: true,
                    regular_block: false,
                    encounter_blocked: false,
                    pending_capital: false,
                },
                &safety,
                &broker,
            )
            .unwrap();
        assert!(matches!(
            decision.actions.as_slice(),
            [Action::Hold { .. }] | [Action::Wait { .. }]
        ));
    }
    let mut next_stores = keys
        .iter()
        .map(|key| {
            (
                key.clone(),
                RetryStore {
                    calls: 0,
                    fail: false,
                    delay: false,
                    stored: vec![],
                },
            )
        })
        .collect();
    let results = candidates
        .commit_accounts(&mut controller, &mut next_stores, 2)
        .await
        .unwrap();
    assert_eq!(results.len(), 2);
    assert!(results.iter().all(|r| r.result.is_ok()));
    controller.acknowledge().unwrap();
    let mut completed = 0;
    let mut polls = 0;
    loop {
        polls += 1;
        assert!(
            polls <= 10,
            "fixture did not complete within its poll budget"
        );
        match controller.poll().unwrap() {
            Poll::Complete => break,
            Poll::Yield => continue,
            Poll::Boundary => {}
            other => panic!("unexpected playback state: {other:?}"),
        }
        candidates.observe(&controller).unwrap();
        let view = controller.decision_view().unwrap();
        let boundary = view.pending().unwrap().unwrap();
        let now = boundary.evaluated_at_ns;
        if matches!(
            boundary.kind,
            arte_core::market_structure::scheduler::Kind::Completed {
                interval_ns: 1_000_000_000,
                ..
            }
        ) {
            completed += 1;
        }
        let snapshot = candidates.features().snapshot().unwrap().unwrap();
        let admission = arte_core::strategy_entry::Admission {
            at_ns: now,
            permissions: true,
            session_open: true,
            tradable: true,
            encounter_blocked: false,
            regular_block: None,
            macd_at_ns: snapshot.macd.as_ref().map(|reading| reading.at_ns),
            macd_positive: snapshot
                .macd
                .as_ref()
                .is_some_and(|reading| reading.positive()),
            detector_at_ns: None,
            detector_fingerprint: String::new(),
            activity_block: snapshot
                .one_second
                .as_ref()
                .and_then(|one| one.activity_block())
                .map(str::to_owned),
        };
        let gates = arte_core::strategy_adds::Gates {
            at_ns: now,
            detector_fresh: false,
            regular_allowed: true,
            no_pending_acquisition: true,
            permission: true,
            tradable: true,
            macd_ready: false,
            no_pending_failed_attempt: true,
            encounter_clear: true,
            range_breakout_allowed: true,
        };
        for scope in &scopes {
            let safety = prepared_account(&scope.account)
                .pending_decision()
                .unwrap()
                .safety
                .clone();
            let decision = candidates
                .prepare_reconciled(
                    &controller,
                    &arte_core::content_hash(scope).unwrap(),
                    crate::playback_runtime::candidates::Evidence {
                        entry: crate::playback_runtime::candidates::EntryEvidence {
                            admission: &admission,
                            swings: &[],
                            regular: true,
                            regular_target: None,
                        },
                        acquisition: arte_core::candidate_features::AcquisitionContext {
                            tradable: true,
                            regular_block: false,
                            encounter_blocked: false,
                            pending_capital: false,
                        },
                        adds: &gates,
                    },
                    &safety,
                    None,
                )
                .unwrap();
            assert!(matches!(
                decision.actions.as_slice(),
                [Action::Hold { .. }] | [Action::Wait { .. }]
            ));
        }
        for store in next_stores.values_mut() {
            store.stored.clear();
        }
        assert!(candidates
            .commit_accounts(&mut controller, &mut next_stores, 2)
            .await
            .unwrap()
            .iter()
            .all(|r| r.result.is_ok()));
        controller.acknowledge().unwrap();
    }
    assert_eq!(completed, 1);
}

#[test]
fn controller_capture_pins_execution_playback_and_action_progress() {
    use arte_core::{
        execution_positions::{checkpoint::Limits as ProjectionLimits, Projection},
        portfolio::checkpoint::Cut,
        simulated_execution::Simulator,
    };
    let (run, costs, manifest, recovery) = run_recovery_fixture(true, false, false);
    let mut execution = crate::simulation_runtime::Runtime::new(
        Simulator::new_scoped("run", 1, 2, 2, 10000).unwrap(),
        Projection::new(2, 10, 4).unwrap(),
        4,
    )
    .unwrap();
    execution
        .bind_source(run.market().unwrap().source_scope())
        .unwrap();
    let mut controller =
        crate::playback_runtime::Runtime::new(run, execution, crate::test_fill_model(), costs)
            .unwrap();
    let limits = crate::simulation_runtime::checkpoint::Limits {
        maximum_bytes: 1_000_000,
        maximum_orders: 2,
        maximum_pending_fills: 4,
        projection: ProjectionLimits {
            positions: 2,
            fills: 10,
            lots_per_position: 4,
            bytes: 100_000,
        },
    };
    let fills = std::collections::BTreeMap::new();
    let mut cut = Cut {
        boundary_sequence: 1,
        boundary_hash: "a".repeat(64),
        at_ns: 0,
    };
    assert!(controller
        .checkpoint(&manifest, &cut, &fills, limits, 10_000_000)
        .is_err());
    controller.resume().unwrap();
    assert_eq!(controller.poll().unwrap(), Poll::Boundary);
    let boundary = controller
        .decision_view()
        .unwrap()
        .pending()
        .unwrap()
        .unwrap();
    cut.boundary_hash = boundary.id.into();
    cut.at_ns = boundary.evaluated_at_ns;
    let input = boundary.input("features".into());
    let scope = controller.decision_view().unwrap().scopes()[0].clone();
    let template = prepared_account(&scope.account);
    let safety = template.pending_decision().unwrap().safety.clone();
    let mut transaction =
        arte_core::strategy_transaction::Runtime::new(scope, 0_u64, 1024).unwrap();
    transaction
        .prepare(input, &safety, "evidence".into(), |_| {
            Ok(vec![Action::CancelEntry {
                reason: "test".into(),
            }])
        })
        .unwrap();
    let rows = transaction.pending_batch().unwrap().records().to_vec();
    let receipt = transaction.acknowledge(&rows).unwrap();
    crate::strategy_journal::accounts::Boundary::record(&mut controller, &receipt).unwrap();
    let image = controller
        .checkpoint(&manifest, &cut, &fills, limits, 10_000_000)
        .unwrap();
    image.root.verify().unwrap();
    let root: serde_json::Value = serde_json::from_slice(&image.root.payload).unwrap();
    assert_eq!(root["actions"].as_array().unwrap().len(), 1);
    assert!(root["actions"][0]["completed_request"].is_null());
    assert_eq!(root["playback"], image.playback.root.id);
    assert_eq!(root["execution"], image.execution.root.id);
    assert!(controller.acknowledge().is_err());
    controller
        .cancel_entry_action(&receipt.decision().decision_id, 0)
        .unwrap();
    let mut completed = controller
        .checkpoint(&manifest, &cut, &fills, limits, 10_000_000)
        .unwrap();
    let root: serde_json::Value = serde_json::from_slice(&completed.root.payload).unwrap();
    assert!(root["actions"][0]["completed_request"].is_string());
    assert_ne!(image.root.id, completed.root.id);
    let context =
        arte_core::content_hash(&("arte.playback-controller-cut.v1", manifest.hash(), &cut))
            .unwrap();
    let restore = |bundle: &crate::playback_runtime::checkpoint::Bundle,
                   receipts: &[&arte_core::strategy_transaction::Committed]| {
        crate::playback_runtime::Runtime::restore_checkpoint(
            bundle,
            &bundle.root.id,
            &manifest,
            &cut,
            &recovery.catalog,
            recovery.prepared.clone(),
            arte_core::market_structure::scheduler::checkpoint::Request {
                context_hash: &context,
                run_id: "run",
                seed_hash: &recovery.seed_hash,
                configuration_hash: &recovery.configuration_hash,
                quote_policy: std::sync::Arc::new(crate::test_quote_policy()),
                maximum_pending: 10,
                maximum_bytes: 10_000_000,
            },
            1,
            2,
            receipts,
            run_with_costs(true, false, false).1,
            limits,
            10_000_000,
        )
    };
    assert!(restore(&image, &[]).is_err());
    let mut pending_restore = restore(&image, &[&receipt]).unwrap();
    assert_eq!(
        pending_restore.status().mode,
        arte_core::market_structure::scheduler::playback::Mode::Paused
    );
    assert_eq!(pending_restore.pending_actions().len(), 1);
    assert_eq!(
        pending_restore
            .checkpoint(&manifest, &cut, &fills, limits, 10_000_000)
            .unwrap()
            .root
            .id,
        image.root.id
    );
    pending_restore
        .cancel_entry_action(&receipt.decision().decision_id, 0)
        .unwrap();
    assert_eq!(
        pending_restore
            .checkpoint(&manifest, &cut, &fills, limits, 10_000_000)
            .unwrap()
            .root
            .id,
        completed.root.id
    );
    let mut complete_restore = restore(&completed, &[&receipt]).unwrap();
    assert!(complete_restore.pending_actions().is_empty());
    complete_restore
        .cancel_entry_action(&receipt.decision().decision_id, 0)
        .unwrap();
    assert_eq!(
        complete_restore
            .checkpoint(&manifest, &cut, &fills, limits, 10_000_000)
            .unwrap()
            .root
            .id,
        completed.root.id
    );
    assert!(complete_restore.acknowledge().is_err()); // Other account still has no receipt.
    let original_root = completed.root.payload.clone();
    let text = String::from_utf8(original_root.clone()).unwrap();
    for (from, to) in [
        (
            arte_core::content_hash(receipt.decision()).unwrap(),
            "0".repeat(64),
        ),
        (
            "\"maximum_quote_age_ns\":2000000000".into(),
            "\"maximum_quote_age_ns\":1".into(),
        ),
    ] {
        let changed = text.replace(&from, &to);
        assert_ne!(changed, text);
        completed.root = arte_core::seed_storage::Object::new(changed.into_bytes());
        assert!(restore(&completed, &[&receipt]).is_err());
    }
    completed.root = arte_core::seed_storage::Object::new(original_root);
    assert!(controller
        .checkpoint(&manifest, &cut, &fills, limits, 100)
        .is_err());
    cut.boundary_sequence += 1;
    assert!(controller
        .checkpoint(&manifest, &cut, &fills, limits, 10_000_000)
        .is_err());
}

#[tokio::test]
async fn committed_execution_action_still_blocks_market_acknowledgment() {
    check_action_gate(false, false).await;
}

#[tokio::test(start_paused = true)]
async fn exit_without_matching_exposure_retains_action_and_boundary() {
    check_action_gate(true, false).await;
}

#[tokio::test(start_paused = true)]
async fn protection_without_matching_exposure_retains_action_and_boundary() {
    check_action_gate(false, true).await;
}

async fn check_action_gate(exit: bool, protection: bool) {
    use arte_core::{execution_positions::Projection, simulated_execution::Simulator};
    let (run, costs) = run_with_costs(false, false, false);
    let mut execution = crate::simulation_runtime::Runtime::new(
        Simulator::new_scoped("run", 1, 2, 2, 10000).unwrap(),
        Projection::new(2, 10, 4).unwrap(),
        4,
    )
    .unwrap();
    execution
        .bind_source(run.market().unwrap().source_scope())
        .unwrap();
    let mut controller =
        crate::playback_runtime::Runtime::new(run, execution, crate::test_fill_model(), costs)
            .unwrap();
    controller.resume().unwrap();
    assert_eq!(controller.poll().unwrap(), Poll::Boundary);
    let view = controller.decision_view().unwrap();
    let input = view.pending().unwrap().unwrap().input("features".into());
    let mut runtimes: Vec<_> = view
        .scopes()
        .iter()
        .map(|scope| {
            let mut runtime =
                arte_core::strategy_transaction::Runtime::new(scope.clone(), 0_u64, 1024).unwrap();
            let template = prepared_account(&scope.account);
            let action = if scope.account == "b" {
                if protection {
                    Action::ReplaceTarget(arte_core::strategy_protection::TargetProposal {
                        target: arte_core::strategy_protection::ActiveTarget::Official {
                            price: 11.0,
                        },
                        triggering_breakout: None,
                        at_ns: input.evaluated_at_ns,
                    })
                } else if exit {
                    Action::Exit {
                        reason: arte_core::strategy_dispatch::ExitReason::ManualExit,
                        quantity: 1,
                        reduce_only: true,
                    }
                } else {
                    Action::CancelEntry {
                        reason: "fixture".into(),
                    }
                }
            } else {
                Action::Hold {
                    reason: "fixture".into(),
                }
            };
            let mut safety = template.pending_decision().unwrap().safety.clone();
            // Deliberately stale strategy exposure must not authorize a simulated short.
            if (exit || protection) && scope.account == "b" {
                safety.position_quantity = 1;
            }
            runtime
                .prepare(input.clone(), &safety, "evidence".into(), |_| {
                    Ok(vec![action])
                })
                .unwrap();
            runtime
        })
        .collect();
    let mut stores = [timed(0, false), timed(0, false)];
    let mut writes: Vec<_> = runtimes
        .iter_mut()
        .zip(stores.iter_mut())
        .map(|(r, p)| accounts::Write::new(r, p))
        .collect();
    assert!(accounts::commit_accounts(&mut writes, &mut controller, 2)
        .await
        .unwrap()
        .iter()
        .all(|o| o.result.is_ok()));
    assert_eq!(controller.decision_view().unwrap().remaining(), Some(0));
    assert_eq!(controller.pending_actions().len(), 1);
    assert_eq!(controller.pending_actions()[0].account, "b");
    assert_eq!(
        controller.pending_actions()[0].kind,
        if protection {
            "replace_target"
        } else if exit {
            "exit"
        } else {
            "cancel_entry"
        }
    );
    assert!(controller.acknowledge().is_err());
    assert_eq!(controller.status().acknowledged_boundaries, 0);
    assert!(accounts::commit_accounts(&mut writes, &mut controller, 2)
        .await
        .unwrap()
        .iter()
        .all(|o| o.result.is_ok()));
    assert_eq!(controller.pending_actions().len(), 1);
    let action = controller.pending_actions()[0].clone();
    if protection {
        let calendar = arte_core::session::Session {
            exchange: "XNYS".into(),
            session: 20260915,
            previous_trading_session: 20260914,
            extended: arte_core::coverage::Interval {
                start: 1,
                end: 300_000_000_000,
            },
            regular: arte_core::coverage::Interval {
                start: 220_000_000_000,
                end: 250_000_000_000,
            },
            available_at_ns: 0,
            source_manifest_hash: "a".repeat(64),
        };
        let hash = arte_core::content_hash(&calendar).unwrap();
        let session = arte_core::orders::TradingSession::new(calendar, hash, 0, true).unwrap();
        let risk = arte_core::orders::RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 2,
            max_band_age_ns: 1_000_000_000,
        };
        for _ in 0..2 {
            assert!(controller
                .protection_action(
                    &action.decision_id,
                    action.action_index,
                    crate::simulation_runtime::AmendmentSafety {
                        session: &session,
                        risk_policy: &risk,
                        bands: None
                    }
                )
                .is_err());
            assert_eq!(controller.pending_actions().len(), 1);
            assert!(controller.acknowledge().is_err());
            assert_eq!(controller.status().acknowledged_boundaries, 0);
        }
        return;
    }
    if exit {
        for _ in 0..2 {
            assert!(controller
                .exit_action(&action.decision_id, action.action_index)
                .is_err());
            assert_eq!(controller.pending_actions().len(), 1);
            assert!(controller.acknowledge().is_err());
            assert_eq!(controller.status().acknowledged_boundaries, 0);
        }
        return;
    }
    controller
        .cancel_entry_action(&action.decision_id, action.action_index)
        .unwrap();
    controller
        .cancel_entry_action(&action.decision_id, action.action_index)
        .unwrap();
    assert!(controller.pending_actions().is_empty());
    controller.acknowledge().unwrap();
    assert_eq!(controller.status().acknowledged_boundaries, 1);
}

#[tokio::test(start_paused = true)]
async fn playback_commits_concurrently_and_retries_only_failed_accounts() {
    let mut run = run_with_quote(false);
    run.resume().unwrap();
    assert_eq!(run.poll().unwrap(), Poll::Boundary);
    let input = run.pending().unwrap().unwrap().input("features".into());
    let mut runtimes: Vec<_> = run
        .scopes()
        .iter()
        .map(|scope| {
            let mut runtime =
                arte_core::strategy_transaction::Runtime::new(scope.clone(), 0_u64, 1024).unwrap();
            let template = prepared_account(&scope.account);
            let decision = template.pending_decision().unwrap();
            runtime
                .prepare(
                    input.clone(),
                    &decision.safety,
                    "evidence".into(),
                    |state| {
                        *state += 1;
                        Ok(vec![Action::Wait {
                            reason: "gate".into(),
                        }])
                    },
                )
                .unwrap();
            runtime
        })
        .collect();
    let mut stores = [timed(1, false), timed(1, true)];
    let mut writes: Vec<_> = runtimes
        .iter_mut()
        .zip(stores.iter_mut())
        .map(|(r, p)| accounts::Write::new(r, p))
        .collect();
    let outcomes = accounts::commit_accounts(&mut writes, &mut run, 2)
        .await
        .unwrap();
    assert!(outcomes[0].result.is_ok());
    assert!(outcomes[1].result.is_err());
    assert_eq!(run.remaining(), Some(1));
    assert!(run.acknowledge().is_err());
    assert_eq!(run.pending().unwrap().unwrap().id, input.event_id);
    assert!(accounts::commit_accounts(&mut writes, &mut run, 2)
        .await
        .unwrap()
        .iter()
        .all(|o| o.result.is_ok()));
    run.acknowledge().unwrap();
    drop(writes);
    assert_eq!(stores.map(|s| s.calls), [1, 2]);
    assert_eq!(run.status().acknowledged_boundaries, 1);
    assert!(runtimes.iter().all(|r| *r.committed_state() == 1));
}
