use super::*;
mod lifecycle;
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

#[tokio::test]
async fn released_playback_quote_drives_one_fill_with_retryable_journal() {
    use arte_core::{execution_positions::Projection, simulated_execution::Simulator};
    let run = run_with_quote(true);
    let source = run.market().unwrap().source_scope();
    let mut simulator = Simulator::new_scoped("run", 1, 2, 2, 10000).unwrap();
    // Test-only preloaded order: production submission still uses funding/session gates.
    simulator
        .submit(
            arte_core::orders::Bracket {
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
            },
            200_000_000_000,
            0,
        )
        .unwrap();
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
    assert!(
        crate::playback_runtime::Runtime::new(run_with_quote(true), foreign, 2_000_000_000)
            .is_err()
    );
    let mut controller =
        crate::playback_runtime::Runtime::new(run, execution, 2_000_000_000).unwrap();
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
    assert!(controller.decision_view().is_err());
    assert_eq!(controller.execution_status().pending_fills, 1);
    assert!(controller.commit_fills(&mut store).await.unwrap());
    assert_eq!(controller.execution_status().pending_fills, 0);
    assert_eq!(controller.poll().unwrap(), Poll::Boundary);
    assert_eq!(controller.execution_status().pending_fills, 0);
    assert_eq!(store.calls, 2);
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
            additional_timeframes: vec![],
            structure: StreamPolicy {
                input_generation: "offline".into(),
                ..StreamPolicy::default()
            },
        },
        &SplitAdjustment::default(),
    )
    .unwrap();
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
            fill_model_hash: "1".repeat(64),
            cost_model_hash: "2".repeat(64),
        },
        consumers: ["a", "b"]
            .into_iter()
            .map(|account| Consumer {
                account: account.into(),
                instrument: 1,
                strategy_instance: "s".into(),
                effective_config_hash: "3".repeat(64),
            })
            .collect(),
    };
    let hash = m.hash().unwrap();
    Run::new(
        &Pinned::new(m, &hash).unwrap(),
        &catalog,
        scheduler,
        prepared,
        1,
        2,
    )
    .unwrap()
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
    let run = run_with_quote(false);
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
        crate::playback_runtime::Runtime::new(run, execution, 2_000_000_000).unwrap();
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
