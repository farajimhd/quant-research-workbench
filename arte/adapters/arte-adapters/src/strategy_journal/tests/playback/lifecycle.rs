//! Integration fixture for execution plumbing, not candidate profitability/parity.
use super::*;
use arte_core::{
    content_hash, decision_orders,
    execution_positions::{Key, Projection},
    order_funding,
    orders::{RiskPolicy, TradingSession},
    portfolio::{Account, Portfolio},
    simulated_execution::{Simulator, MODEL},
    strategy_protection::{ActiveTarget, TargetProposal},
};
use std::collections::BTreeMap;

struct Fills {
    fail: bool,
    rows: BTreeMap<String, String>,
}
#[derive(Default)]
struct Decisions {
    rows: Vec<Record>,
}
async fn coordinate(
    controller: &mut crate::playback_runtime::Runtime,
    candidates: &mut crate::playback_runtime::candidates::Candidates,
    actions: crate::playback_runtime::SizedActionInputs<'_>,
    currencies: &BTreeMap<u64, arte_core::simulation_costs::SettlementCurrency>,
) -> crate::playback_runtime::runner::Step {
    // This fixture uses manually committed strategy intents. The coordinator
    // consumes their real receipts; it does not fabricate candidate decisions.
    let mut decisions = candidates
        .scope_hashes()
        .map(|scope| (scope.to_owned(), Decisions::default()))
        .collect();
    let mut fills = BTreeMap::<String, Fills>::new();
    controller
        .service_boundary(
            candidates,
            crate::playback_runtime::runner::Inputs {
                actions,
                currencies,
                maximum_funding_orders: 1,
                maximum_settlement_receipts: 10,
                decision_concurrency: 2,
            },
            crate::playback_runtime::runner::Journals {
                fills: &mut fills,
                decisions: &mut decisions,
                rejections: &mut RejectionJournalUnavailable,
            },
        )
        .await
        .unwrap()
}
struct RejectionJournalUnavailable;
struct RejectionJournalPending;
impl crate::rejection_journal::Publisher for RejectionJournalPending {
    async fn append(
        &mut self,
        _: &arte_core::strategy_transaction::Committed,
        _: &arte_core::action_rejection::Record,
    ) -> Result<arte_core::action_rejection::Record> {
        std::future::pending().await
    }
}
impl crate::rejection_journal::Publisher for RejectionJournalUnavailable {
    async fn append(
        &mut self,
        _: &arte_core::strategy_transaction::Committed,
        _: &arte_core::action_rejection::Record,
    ) -> Result<arte_core::action_rejection::Record> {
        Err(Error::Unready(
            "fixture rejection journal unavailable".into(),
        ))
    }
}
impl Publisher for Decisions {
    async fn append(&mut self, batch: &Batch) -> Result<Vec<Record>> {
        self.rows.extend_from_slice(batch.records());
        // Readback is scoped to this publication, not the complete account journal.
        Ok(batch.records().to_vec())
    }
}
impl crate::fill_journal::Publisher for Fills {
    async fn publish(
        &mut self,
        batch: &crate::fill_journal::Batch,
    ) -> Result<BTreeMap<String, String>> {
        self.rows.extend(batch.rows().clone());
        if self.fail {
            self.fail = false;
            return Err(Error::Unready("ambiguous fill publication".into()));
        }
        Ok(batch.rows().clone())
    }
}

#[tokio::test]
async fn funded_multiaccount_entry_protection_exit_and_journal_feedback() {
    lifecycle(false, false, Scenario::Normal).await;
}
#[tokio::test]
async fn replacement_target_controls_later_fills_not_the_original_target() {
    lifecycle(true, false, Scenario::Normal).await;
}
#[tokio::test]
async fn cancelled_unfilled_orders_release_exact_funding_without_fabricated_cash() {
    lifecycle(false, true, Scenario::Normal).await;
}
#[tokio::test]
async fn failed_owned_submission_retains_funding_and_rejects_changed_retry() {
    lifecycle(false, false, Scenario::FailedSubmission).await;
}
#[tokio::test]
async fn sequential_sizing_does_not_spend_reserved_cash_twice() {
    lifecycle(false, false, Scenario::SharedCash).await;
}
#[tokio::test]
async fn sized_submission_failure_returns_allocation_for_exact_retry() {
    lifecycle(false, false, Scenario::Capacity).await;
}
#[tokio::test]
async fn coordinator_drives_funded_entries_protection_exits_and_settlement() {
    lifecycle(false, false, Scenario::Coordinator).await;
}
#[tokio::test]
async fn coordinator_settles_target_fills_after_protection_replacement() {
    lifecycle(true, false, Scenario::Coordinator).await;
}
#[derive(Clone, Copy)]
enum Scenario {
    Normal,
    FailedSubmission,
    SharedCash,
    Capacity,
    Coordinator,
}
async fn lifecycle(target_exit: bool, cancel_unfilled: bool, scenario: Scenario) {
    let fail_submission = matches!(scenario, Scenario::FailedSubmission);
    let (run, costs, manifest, recovery) = run_configured_fixture(
        true,
        true,
        target_exit,
        matches!(scenario, Scenario::Coordinator),
        matches!(scenario, Scenario::SharedCash),
    );
    let cost_model = costs.model().clone();
    let mut runtimes: Vec<_> = run
        .scopes()
        .iter()
        .map(|scope| {
            arte_core::strategy_transaction::Runtime::new(scope.clone(), 0_u64, 1024).unwrap()
        })
        .collect();
    let mut execution = crate::simulation_runtime::Runtime::new(
        Simulator::new_scoped(
            "run",
            1,
            2,
            if matches!(scenario, Scenario::Capacity) {
                1
            } else {
                4
            },
            10000,
        )
        .unwrap(),
        Projection::new(2, 20, 8).unwrap(),
        8,
    )
    .unwrap();
    execution
        .bind_source(run.market().unwrap().source_scope())
        .unwrap();
    let mut controller =
        crate::playback_runtime::Runtime::new(run, execution, crate::test_fill_model(), costs)
            .unwrap();
    let mut coordinator_candidates = matches!(scenario, Scenario::Coordinator).then(|| {
        crate::playback_runtime::candidates::Candidates::new(
            &controller,
            &manifest,
            super::policies::config("a").features,
            100_000,
        )
        .unwrap()
    });
    let portfolio = Portfolio::new(
        [("a", 2000), ("b", 4000)]
            .into_iter()
            .map(|(id, budget)| {
                (
                    id.into(),
                    Account {
                        currency: "USD".into(),
                        currency_scale: 2,
                        simulation_run_id: Some("run".into()),
                        budget_minor: budget,
                        broker_available_minor: budget,
                        balance_at_ns: 200_000_000_000,
                        max_balance_age_ns: 10_000_000_000,
                        reservations: BTreeMap::new(),
                    },
                )
            })
            .collect(),
    )
    .unwrap();
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
    let hash = content_hash(&calendar).unwrap();
    let session = TradingSession::new(calendar, hash, 0, true).unwrap();
    let risk = RiskPolicy {
        band_provider: 1,
        band_session: 20260915,
        band_buffer_ticks: 2,
        max_band_age_ns: 1_000_000_000,
    };
    let cash = order_funding::Policy {
        currency_scale: 2,
        maximum_order_cash_minor: 4000,
        maximum_order_risk_minor: 1000,
        fee_reserve_minor: 1,
    };
    let currency = arte_core::simulation_costs::SettlementCurrency {
        instrument: 1,
        currency: "USD".into(),
        available_at_ns: 200_000_000_000,
        reference_manifest_hash: "b".repeat(64),
    };
    let mut fills = Fills {
        fail: true,
        rows: BTreeMap::new(),
    };
    let mut stores: Vec<_> = (0..runtimes.len()).map(|_| Decisions::default()).collect();
    let mut quotes = 0;
    let mut entries = 0;
    let mut exits = 0;
    let mut commands: Vec<String> = Vec::new();
    let mut released = false;
    controller.resume().unwrap();
    let mut completed = false;
    for _ in 0..12 {
        let poll = controller.poll().unwrap();
        if poll == Poll::Complete {
            completed = true;
            break;
        }
        if poll == Poll::Yield {
            continue;
        }
        assert_eq!(poll, Poll::Boundary);
        if controller.execution_status().pending_fills > 0 {
            assert!(controller.decision_view().is_err());
            assert!(controller.account_view("a").is_err());
            assert!(controller.account_view("b").is_err());
            assert!(controller.acknowledge().is_err());
            assert!(controller
                .reconcile_funding(&portfolio, &BTreeMap::from([(1, currency.clone())]), 2, 10)
                .is_err());
            for command in &commands {
                assert!(controller
                    .release_unfilled_reservation(command, &portfolio)
                    .is_err());
                assert!(controller.closed_net_cash_minor(command).is_err());
                assert!(controller
                    .settle_closed_order(command, &portfolio, &currency, 10)
                    .is_err());
            }
            if fills.fail {
                assert!(controller.commit_fills(&mut fills).await.is_err());
                assert!(controller.decision_view().is_err());
                assert!(controller.account_view("a").is_err());
            }
            while controller.commit_fills(&mut fills).await.unwrap() {}
        }
        let view = controller.decision_view().unwrap();
        let boundary = view.pending().unwrap().unwrap();
        let quote = matches!(
            boundary.kind,
            arte_core::market_structure::scheduler::Kind::Quote { .. }
        );
        if quote {
            quotes += 1;
        }
        let input = boundary.input("fixture-features".into());
        let now = input.evaluated_at_ns;
        if !cancel_unfilled && quotes < 4 {
            assert!(controller
                .reconcile_funding(&portfolio, &BTreeMap::from([(1, currency.clone())]), 2, 10)
                .unwrap()
                .is_empty());
        }
        if quote && quotes == 4 && !cancel_unfilled {
            assert!(controller
                .reconcile_funding(&portfolio, &BTreeMap::new(), 0, 10)
                .is_err());
            let missing = controller
                .reconcile_funding(&portfolio, &BTreeMap::new(), 2, 10)
                .unwrap();
            assert_eq!(missing.len(), 2);
            assert!(missing.iter().all(|o| o.result.is_err()));
            let mut settled_commands = std::collections::BTreeSet::new();
            for command in &commands {
                let mut wrong = currency.clone();
                wrong.currency = "CAD".into();
                assert!(controller
                    .settle_closed_order(command, &portfolio, &wrong, 10)
                    .is_err());
                let outcomes = if let Some(candidates) = coordinator_candidates.as_mut() {
                    let step = coordinate(
                        &mut controller,
                        candidates,
                        crate::playback_runtime::SizedActionInputs {
                            sizing: &BTreeMap::new(),
                            cash_policies: &BTreeMap::new(),
                            portfolio: &portfolio,
                            safety: crate::simulation_runtime::AmendmentSafety {
                                session: &session,
                                risk_policy: &risk,
                                bands: None,
                            },
                            latency_ns: 0,
                            maximum_actions: 1,
                        },
                        &BTreeMap::from([(1, currency.clone())]),
                    )
                    .await;
                    let crate::playback_runtime::runner::Step::Funding(outcomes) = step else {
                        panic!("closed positions must settle before new evaluation or execution");
                    };
                    outcomes
                } else {
                    controller
                        .reconcile_funding(
                            &portfolio,
                            &BTreeMap::from([(1, currency.clone())]),
                            1,
                            10,
                        )
                        .unwrap()
                };
                assert_eq!(outcomes.len(), 1);
                assert_eq!(
                    outcomes[0].kind,
                    crate::simulation_runtime::funding::Kind::SettleClosed
                );
                let outcome = outcomes.into_iter().next().unwrap();
                assert!(outcome.result.unwrap());
                assert!(!controller
                    .settle_closed_order(&outcome.command_id, &portfolio, &currency, 10)
                    .unwrap());
                assert!(settled_commands.insert(outcome.command_id));
            }
            assert_eq!(settled_commands, commands.iter().cloned().collect());
            assert!(controller
                .reconcile_funding(&portfolio, &BTreeMap::from([(1, currency.clone())]), 2, 10)
                .unwrap()
                .is_empty());
        }
        for command in &commands {
            if released {
                assert!(!controller
                    .release_unfilled_reservation(command, &portfolio)
                    .unwrap());
            } else {
                assert!(controller
                    .release_unfilled_reservation(command, &portfolio)
                    .is_err());
            }
        }
        for runtime in &mut runtimes {
            let account = &runtime.scope().account;
            let key = position_key(account);
            let view = controller.account_view(account).unwrap();
            assert_eq!(view.evaluated_at_ns, now);
            assert_eq!(view.account, account);
            assert_eq!(view.instrument, runtime.scope().instrument);
            assert!(view
                .orders
                .iter()
                .all(|owned| owned.scope == runtime.scope()));
            assert!(controller.account_view("foreign").is_err());
            let quantity = view.position.map_or(0, |p| p.quantity);
            let attributed = controller.strategy_position(runtime.scope()).unwrap();
            assert_eq!(attributed.map_or(0, |p| p.quantity), quantity);
            if let (Some(owned), Some(account_position)) = (attributed, view.position) {
                assert_eq!(owned.open_cost_atoms, account_position.open_cost_atoms);
                assert_eq!(
                    owned.realized_gross_pnl_atoms,
                    account_position.realized_gross_pnl_atoms
                );
            }
            assert_eq!(
                quantity,
                controller.position(&key).map_or(0, |p| p.quantity)
            );
            assert_eq!(
                quantity,
                view.orders
                    .iter()
                    .map(|owned| owned.order.entry_filled - owned.order.exit_filled)
                    .sum::<u64>()
            );
            let mut safety = prepared_account(account)
                .pending_decision()
                .unwrap()
                .safety
                .clone();
            safety.position_quantity = quantity;
            let target = ActiveTarget::Official {
                price: if quotes >= 3 { 11.5 } else { 11. },
            };
            let reconciled = controller
                .owned_candidate_position(runtime.scope())
                .unwrap();
            assert_eq!(reconciled.position.quantity, quantity);
            assert_eq!(reconciled.position.at_ns, now);
            if quantity > 0 {
                assert_eq!(reconciled.position.average_price, Some(10.01));
                assert_eq!(
                    reconciled.position.target.as_ref().unwrap().price(),
                    target.price()
                );
                assert!(controller
                    .candidate_position(runtime.scope(), None)
                    .is_err());
                assert!(controller
                    .candidate_position(
                        runtime.scope(),
                        Some(&ActiveTarget::Official { price: 999. })
                    )
                    .is_err());
            } else {
                assert!(reconciled.position.average_price.is_none());
                assert!(reconciled.position.stop.is_none());
                assert!(reconciled.position.target.is_none());
            }
            let mut inconsistent = safety.clone();
            inconsistent.position_quantity = 999;
            inconsistent.pending_entry = !reconciled.position.pending_entry;
            let corrected = reconciled.safety(&inconsistent);
            assert_eq!(corrected.position_quantity, quantity);
            assert_eq!(corrected.pending_entry, reconciled.position.pending_entry);
            assert_eq!(corrected.flatten, safety.flatten);
            let action = if quote && quotes == 1 {
                Action::Enter(Box::new(proposal(now)))
            } else if cancel_unfilled && !quote && quotes == 1 {
                Action::CancelEntry {
                    reason: "cancel-before-fill".into(),
                }
            } else if quote && quotes == 2 && !cancel_unfilled {
                assert_eq!(quantity, if account == "a" { 1 } else { 2 });
                Action::ReplaceTarget(TargetProposal {
                    target: ActiveTarget::Official { price: 11.5 },
                    triggering_breakout: None,
                    at_ns: now,
                })
            } else if quote && quotes == 3 && !target_exit && !cancel_unfilled {
                Action::Exit {
                    reason: ExitReason::ManualExit,
                    quantity,
                    reduce_only: true,
                }
            } else {
                if quote && quotes == 3 && target_exit {
                    assert_eq!(quantity, if account == "a" { 1 } else { 2 });
                }
                Action::Hold {
                    reason: "fixture".into(),
                }
            };
            runtime
                .prepare(input.clone(), &safety, "execution-fixture".into(), |_| {
                    Ok(vec![action])
                })
                .unwrap();
        }
        let mut writes: Vec<_> = runtimes
            .iter_mut()
            .zip(stores.iter_mut())
            .map(|(r, p)| accounts::Write::new(r, p))
            .collect();
        for outcome in accounts::commit_accounts(&mut writes, &mut controller, 2)
            .await
            .unwrap()
        {
            outcome.result.unwrap();
        }
        if matches!(scenario, Scenario::SharedCash | Scenario::Capacity) {
            let sizing = crate::playback_runtime::Sizing {
                price_scale: 2,
                tick: 1,
                maximum_quantity: 100,
                lot_size: 1,
                order_lifetime_ns: 1_000_000_000,
            };
            let request = crate::playback_runtime::SizingRequest {
                sizing: &sizing,
                portfolio: &portfolio,
                cash_policy: &cash,
                safety: crate::simulation_runtime::AmendmentSafety {
                    session: &session,
                    risk_policy: &risk,
                    bands: None,
                },
                latency_ns: 0,
            };
            for write in &writes {
                let decision = write.receipt().unwrap().decision();
                assert!(matches!(decision.actions[0], Action::Enter(_)));
                if decision.scope.strategy_instance == "t" {
                    let before = portfolio.snapshot("a").unwrap().reservations;
                    let assessment = controller
                        .assess_entry_action(
                            &decision.decision_id,
                            0,
                            crate::playback_runtime::SizingRequest {
                                sizing: &sizing,
                                portfolio: &portfolio,
                                cash_policy: &cash,
                                safety: crate::simulation_runtime::AmendmentSafety {
                                    session: &session,
                                    risk_policy: &risk,
                                    bands: None,
                                },
                                latency_ns: 0,
                            },
                        )
                        .unwrap();
                    let crate::playback_runtime::EntryAssessment::Rejected(calculation) =
                        assessment
                    else {
                        panic!("second strategy cannot fund a lot from remaining cash");
                    };
                    assert_eq!(
                        calculation.outcome().unwrap(),
                        arte_core::order_funding::sizing::Outcome::Rejected(
                            arte_core::order_funding::sizing::Rejection::NoApprovedLot
                        )
                    );
                    crate::rejection_journal::exercise(
                        write.receipt().unwrap(),
                        calculation.clone(),
                    )
                    .await;
                    assert!(controller
                        .allocate_and_enter_action(&decision.decision_id, 0, &request)
                        .is_err());
                    assert_eq!(portfolio.snapshot("a").unwrap().reservations, before);
                    assert_eq!(before.len(), 1);
                    continue;
                }
                let attempt = controller
                    .allocate_and_enter_action(&decision.decision_id, 0, &request)
                    .unwrap();
                if matches!(scenario, Scenario::Capacity) && decision.scope.account == "b" {
                    assert!(attempt.result.is_err());
                    assert_eq!(attempt.allocation.quantity, 3);
                    assert_eq!(
                        content_hash(
                            controller
                                .retained_entry_allocation(&decision.decision_id, 0)
                                .unwrap()
                                .unwrap()
                        )
                        .unwrap(),
                        content_hash(&attempt.allocation).unwrap()
                    );
                    let reserved = portfolio.snapshot("b").unwrap().reservations;
                    assert_eq!(reserved.len(), 1);
                    assert!(controller
                        .enter_action(
                            &decision.decision_id,
                            0,
                            crate::playback_runtime::EntryRequest {
                                allocation: &attempt.allocation,
                                portfolio: &portfolio,
                                cash_policy: &cash,
                                safety: crate::simulation_runtime::AmendmentSafety {
                                    session: &session,
                                    risk_policy: &risk,
                                    bands: None
                                },
                                latency_ns: 0,
                            }
                        )
                        .is_err());
                    assert!(controller
                        .allocate_and_enter_action(&decision.decision_id, 0, &request)
                        .is_err());
                    assert_eq!(portfolio.snapshot("b").unwrap().reservations, reserved);
                    continue;
                }
                let plan = attempt.result.unwrap();
                assert_eq!(
                    plan.bracket.quantity,
                    if decision.scope.account == "a" { 1 } else { 3 }
                );
                assert_eq!(plan.bracket.quantity, attempt.allocation.quantity);
                let before = portfolio
                    .snapshot(&decision.scope.account)
                    .unwrap()
                    .reservations;
                assert_eq!(before.len(), 1);
                // Already-funded actions require the retained allocation, not a
                // fresh sizing pass using their own reduced account balance.
                assert!(controller
                    .allocate_and_enter_action(&decision.decision_id, 0, &request)
                    .is_err());
                controller
                    .enter_action(
                        &decision.decision_id,
                        0,
                        crate::playback_runtime::EntryRequest {
                            allocation: &attempt.allocation,
                            portfolio: &portfolio,
                            cash_policy: &cash,
                            safety: crate::simulation_runtime::AmendmentSafety {
                                session: &session,
                                risk_policy: &risk,
                                bands: None,
                            },
                            latency_ns: 0,
                        },
                    )
                    .unwrap();
            }
            let pending = controller.pending_actions();
            assert_eq!(pending.len(), 1);
            assert_eq!(
                pending[0].account,
                if matches!(scenario, Scenario::Capacity) {
                    "b"
                } else {
                    "a"
                }
            );
            assert_eq!(pending[0].action_index, 0);
            if matches!(scenario, Scenario::Capacity) {
                let sizing_policies =
                    BTreeMap::from([("a".into(), sizing.clone()), ("b".into(), sizing.clone())]);
                let cash_policies =
                    BTreeMap::from([("a".into(), cash.clone()), ("b".into(), cash.clone())]);
                let before = portfolio.snapshot("b").unwrap().reservations;
                let retained = content_hash(
                    controller
                        .retained_entry_allocation(&pending[0].decision_id, 0)
                        .unwrap()
                        .unwrap(),
                )
                .unwrap();
                let outcomes = controller
                    .execute_journaled_actions(
                        crate::playback_runtime::SizedActionInputs {
                            sizing: &sizing_policies,
                            cash_policies: &cash_policies,
                            portfolio: &portfolio,
                            safety: crate::simulation_runtime::AmendmentSafety {
                                session: &session,
                                risk_policy: &risk,
                                bands: None,
                            },
                            latency_ns: 0,
                            maximum_actions: 2,
                        },
                        &mut RejectionJournalUnavailable,
                    )
                    .await
                    .unwrap();
                assert_eq!(outcomes.len(), 1);
                assert!(outcomes[0].result.is_err());
                assert_eq!(portfolio.snapshot("b").unwrap().reservations, before);
                assert_eq!(
                    content_hash(
                        controller
                            .retained_entry_allocation(&pending[0].decision_id, 0)
                            .unwrap()
                            .unwrap()
                    )
                    .unwrap(),
                    retained
                );
            }
            assert!(controller.acknowledge().is_err());
            if matches!(scenario, Scenario::SharedCash) {
                let id = &pending[0].decision_id;
                let request = || crate::playback_runtime::SizingRequest {
                    sizing: &sizing,
                    portfolio: &portfolio,
                    cash_policy: &cash,
                    safety: crate::simulation_runtime::AmendmentSafety {
                        session: &session,
                        risk_policy: &risk,
                        bands: None,
                    },
                    latency_ns: 0,
                };
                let before = portfolio.snapshot("a").unwrap().reservations;
                let sizing_policies =
                    BTreeMap::from([("a".into(), sizing.clone()), ("b".into(), sizing.clone())]);
                let cash_policies =
                    BTreeMap::from([("a".into(), cash.clone()), ("b".into(), cash.clone())]);
                let dispatch_inputs = || crate::playback_runtime::SizedActionInputs {
                    sizing: &sizing_policies,
                    cash_policies: &cash_policies,
                    portfolio: &portfolio,
                    safety: crate::simulation_runtime::AmendmentSafety {
                        session: &session,
                        risk_policy: &risk,
                        bands: None,
                    },
                    latency_ns: 0,
                    maximum_actions: 1,
                };
                let mut waiting_journal = RejectionJournalPending;
                let mut cancelled = Box::pin(
                    controller.execute_journaled_actions(dispatch_inputs(), &mut waiting_journal),
                );
                assert!(std::future::Future::poll(
                    cancelled.as_mut(),
                    &mut std::task::Context::from_waker(std::task::Waker::noop())
                )
                .is_pending());
                drop(cancelled);
                let retained_hash = controller.rejection_records()[0].hash().unwrap();
                let outcomes = controller
                    .execute_journaled_actions(dispatch_inputs(), &mut RejectionJournalUnavailable)
                    .await
                    .unwrap();
                assert_eq!(outcomes.len(), 1);
                assert!(outcomes[0].result.is_err());
                assert_eq!(controller.rejection_records().len(), 1);
                assert_eq!(
                    controller.rejection_records()[0].hash().unwrap(),
                    retained_hash
                );
                let record = controller
                    .prepare_entry_rejection(id, 0, request())
                    .unwrap()
                    .clone();
                assert_eq!(
                    controller
                        .prepare_entry_rejection(id, 0, request())
                        .unwrap()
                        .hash()
                        .unwrap(),
                    record.hash().unwrap()
                );
                assert!(controller
                    .allocate_and_enter_action(id, 0, &request())
                    .is_err());
                let allocation = arte_core::decision_orders::Allocation {
                    account: "a".into(),
                    instrument: 1,
                    quantity: 1,
                    price_scale: 2,
                    tick: 1,
                    entry_limit: 1001,
                    deadline_ns: now + 1_000_000_000,
                };
                assert!(controller
                    .enter_action(
                        id,
                        0,
                        crate::playback_runtime::EntryRequest {
                            allocation: &allocation,
                            portfolio: &portfolio,
                            cash_policy: &cash,
                            safety: crate::simulation_runtime::AmendmentSafety {
                                session: &session,
                                risk_policy: &risk,
                                bands: None
                            },
                            latency_ns: 0,
                        }
                    )
                    .is_err());
                assert_eq!(portfolio.snapshot("a").unwrap().reservations, before);
                assert!(controller.acknowledge().is_err());
                struct Journal {
                    fail: bool,
                    record: Option<arte_core::action_rejection::Record>,
                }
                impl crate::rejection_journal::Publisher for Journal {
                    async fn append(
                        &mut self,
                        _: &arte_core::strategy_transaction::Committed,
                        record: &arte_core::action_rejection::Record,
                    ) -> Result<arte_core::action_rejection::Record> {
                        self.record = Some(record.clone());
                        if std::mem::take(&mut self.fail) {
                            return Err(Error::Unready("ambiguous fixture write".into()));
                        }
                        Ok(record.clone())
                    }
                }
                let mut journal = Journal {
                    fail: true,
                    record: None,
                };
                assert!(controller
                    .commit_entry_rejection(id, 0, &portfolio, &mut journal)
                    .await
                    .is_err());
                assert!(controller.acknowledge().is_err());
                let outcomes = controller
                    .execute_journaled_actions(dispatch_inputs(), &mut journal)
                    .await
                    .unwrap();
                assert_eq!(outcomes.len(), 1);
                assert!(matches!(&outcomes[0].result,
                    Ok(crate::playback_runtime::Resolution::Rejected { record_hash }) if record_hash == &record.hash().unwrap()));
                assert!(!controller
                    .commit_entry_rejection(id, 0, &portfolio, &mut journal)
                    .await
                    .unwrap());
                assert!(controller.pending_actions().is_empty());
                let cut = arte_core::portfolio::checkpoint::Cut {
                    boundary_sequence: input.source_sequence,
                    boundary_hash: input.event_id.clone(),
                    at_ns: now,
                };
                let limits = crate::simulation_runtime::checkpoint::Limits {
                    maximum_bytes: 1_000_000,
                    maximum_orders: 4,
                    maximum_pending_fills: 8,
                    projection: arte_core::execution_positions::checkpoint::Limits {
                        positions: 3,
                        fills: 100,
                        lots_per_position: 8,
                        bytes: 100_000,
                    },
                };
                let image = controller
                    .checkpoint(&manifest, &cut, &BTreeMap::new(), limits, 2_000_000)
                    .unwrap();
                let context =
                    content_hash(&("arte.playback-controller-cut.v1", manifest.hash(), &cut))
                        .unwrap();
                let receipts: Vec<_> = writes
                    .iter()
                    .map(|write| write.receipt().unwrap())
                    .collect();
                let mut restored = crate::playback_runtime::Runtime::restore_checkpoint(
                    &image,
                    &image.root.id,
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
                        maximum_bytes: 2_000_000,
                    },
                    1,
                    3,
                    &receipts,
                    arte_core::simulation_costs::Pinned::new(cost_model.clone(), &manifest)
                        .unwrap(),
                    limits,
                    2_000_000,
                )
                .unwrap();
                assert_eq!(restored.rejection_records().len(), 1);
                assert_eq!(restored.pending_actions().len(), 1);
                assert!(restored.acknowledge().is_err());
                let receipt = receipts
                    .iter()
                    .find(|receipt| &receipt.decision().decision_id == id)
                    .unwrap();
                let readback = arte_core::action_rejection::Committed::from_readback(
                    receipt,
                    &record.hash().unwrap(),
                    journal.record.unwrap(),
                )
                .unwrap();
                let mut changed = record.clone();
                changed.evidence_hash = "f".repeat(64);
                let conflicting = arte_core::action_rejection::Committed::from_readback(
                    receipt,
                    &changed.hash().unwrap(),
                    changed,
                )
                .unwrap();
                assert!(restored
                    .confirm_entry_rejection(&conflicting, &portfolio)
                    .is_err());
                assert!(restored.acknowledge().is_err());
                restored
                    .confirm_entry_rejection(&readback, &portfolio)
                    .unwrap();
                assert!(restored.pending_actions().is_empty());
                assert_eq!(
                    restored
                        .checkpoint(&manifest, &cut, &BTreeMap::new(), limits, 2_000_000)
                        .unwrap()
                        .root
                        .id,
                    image.root.id
                );
                assert_eq!(portfolio.snapshot("a").unwrap().reservations, before);
                restored.acknowledge().unwrap();
            } else {
                let request = crate::playback_runtime::SizingRequest {
                    sizing: &sizing,
                    portfolio: &portfolio,
                    cash_policy: &cash,
                    safety: crate::simulation_runtime::AmendmentSafety {
                        session: &session,
                        risk_policy: &risk,
                        bands: None,
                    },
                    latency_ns: 0,
                };
                assert!(controller
                    .prepare_entry_rejection(&pending[0].decision_id, 0, request)
                    .is_err());
            }
            return;
        }
        if !fail_submission {
            let allocations: BTreeMap<_, _> = writes
                .iter()
                .filter_map(|write| {
                    let decision = write.receipt().unwrap().decision();
                    matches!(decision.actions[0], Action::Enter(_)).then(|| {
                        let sizing = crate::playback_runtime::Sizing {
                            price_scale: 2,
                            tick: 1,
                            maximum_quantity: if decision.scope.account == "a" {
                                100
                            } else {
                                2
                            },
                            lot_size: 1,
                            order_lifetime_ns: 1_000_000_000,
                        };
                        let request = |sizing| crate::playback_runtime::SizingRequest {
                            sizing,
                            portfolio: &portfolio,
                            cash_policy: &cash,
                            safety: crate::simulation_runtime::AmendmentSafety {
                                session: &session,
                                risk_policy: &risk,
                                bands: None,
                            },
                            latency_ns: 0,
                        };
                        let before = portfolio
                            .snapshot(&decision.scope.account)
                            .unwrap()
                            .reservations;
                        let allocation = controller
                            .allocate_entry_action(&decision.decision_id, 0, request(&sizing))
                            .unwrap();
                        assert_eq!(
                            allocation.quantity,
                            if decision.scope.account == "a" { 1 } else { 2 }
                        );
                        assert_eq!(allocation.entry_limit, 1001);
                        assert_eq!(
                            portfolio
                                .snapshot(&decision.scope.account)
                                .unwrap()
                                .reservations,
                            before
                        );
                        let mut invalid = sizing.clone();
                        invalid.lot_size = 1000;
                        let assessed = controller
                            .assess_entry_action(&decision.decision_id, 0, request(&invalid))
                            .unwrap();
                        let crate::playback_runtime::EntryAssessment::Rejected(calculation) =
                            assessed
                        else {
                            panic!("oversized lot must reject");
                        };
                        assert_eq!(
                            calculation.outcome().unwrap(),
                            arte_core::order_funding::sizing::Outcome::Rejected(
                                arte_core::order_funding::sizing::Rejection::NoApprovedLot
                            )
                        );
                        assert!(controller
                            .allocate_entry_action(&decision.decision_id, 0, request(&invalid))
                            .is_err());
                        let invalid = crate::playback_runtime::Sizing {
                            lot_size: 0,
                            ..sizing.clone()
                        };
                        assert!(controller
                            .assess_entry_action(&decision.decision_id, 0, request(&invalid))
                            .is_err());
                        assert_eq!(
                            portfolio
                                .snapshot(&decision.scope.account)
                                .unwrap()
                                .reservations,
                            before
                        );
                        ((decision.decision_id.clone(), 0), allocation)
                    })
                })
                .collect();
            let policies = BTreeMap::from([("a".into(), cash.clone()), ("b".into(), cash.clone())]);
            let mut incomplete = allocations.clone();
            incomplete.pop_first();
            let inputs = |allocations, maximum_actions| crate::playback_runtime::ActionInputs {
                allocations,
                cash_policies: &policies,
                portfolio: &portfolio,
                safety: crate::simulation_runtime::AmendmentSafety {
                    session: &session,
                    risk_policy: &risk,
                    bands: None,
                },
                latency_ns: 0,
                maximum_actions,
            };
            assert!(controller.execute_actions(inputs(&allocations, 0)).is_err());
            if !allocations.is_empty() && coordinator_candidates.is_none() {
                let outcomes = controller.execute_actions(inputs(&incomplete, 2)).unwrap();
                assert_eq!(outcomes.iter().filter(|o| o.result.is_err()).count(), 1);
                assert_eq!(outcomes.iter().filter(|o| o.result.is_ok()).count(), 1);
                assert!(controller.acknowledge().is_err());
            }
            // Bounded calls drain the remaining work. Successes are never replayed.
            let sizing = BTreeMap::from([
                (
                    "a".into(),
                    crate::playback_runtime::Sizing {
                        price_scale: 2,
                        tick: 1,
                        maximum_quantity: 100,
                        lot_size: 1,
                        order_lifetime_ns: 1_000_000_000,
                    },
                ),
                (
                    "b".into(),
                    crate::playback_runtime::Sizing {
                        price_scale: 2,
                        tick: 1,
                        maximum_quantity: 2,
                        lot_size: 1,
                        order_lifetime_ns: 1_000_000_000,
                    },
                ),
            ]);
            let sized_inputs = || crate::playback_runtime::SizedActionInputs {
                sizing: &sizing,
                cash_policies: &policies,
                portfolio: &portfolio,
                safety: crate::simulation_runtime::AmendmentSafety {
                    session: &session,
                    risk_policy: &risk,
                    bands: None,
                },
                latency_ns: 0,
                maximum_actions: 1,
            };
            while !controller.pending_actions().is_empty() {
                let before = controller.pending_actions().len();
                let outcomes = if let Some(candidates) = coordinator_candidates.as_mut() {
                    let crate::playback_runtime::runner::Step::Actions(outcomes) = coordinate(
                        &mut controller,
                        candidates,
                        sized_inputs(),
                        &BTreeMap::from([(1, currency.clone())]),
                    )
                    .await
                    else {
                        panic!("committed actions must resolve before checkpoint readiness");
                    };
                    outcomes
                } else {
                    controller
                        .execute_journaled_actions(sized_inputs(), &mut RejectionJournalUnavailable)
                        .await
                        .unwrap()
                };
                assert_eq!(outcomes.len(), 1);
                outcomes.into_iter().next().unwrap().result.unwrap();
                assert_eq!(controller.pending_actions().len(), before - 1);
            }
            assert!(controller
                .execute_journaled_actions(sized_inputs(), &mut RejectionJournalUnavailable)
                .await
                .unwrap()
                .is_empty());
            if let Some(candidates) = coordinator_candidates.as_mut() {
                let crate::playback_runtime::runner::Step::CheckpointRequired(cut) = coordinate(
                    &mut controller,
                    candidates,
                    sized_inputs(),
                    &BTreeMap::from([(1, currency.clone())]),
                )
                .await
                else {
                    panic!("fully resolved boundary must wait for its checkpoint");
                };
                assert_eq!(cut.at_ns, now);
                assert_eq!(cut.boundary_hash, input.event_id);
            }
            for (id, index) in allocations.keys() {
                assert!(controller
                    .allocate_entry_action(
                        id,
                        *index,
                        crate::playback_runtime::SizingRequest {
                            sizing: &crate::playback_runtime::Sizing {
                                price_scale: 2,
                                tick: 1,
                                maximum_quantity: 100,
                                lot_size: 1,
                                order_lifetime_ns: 1_000_000_000
                            },
                            portfolio: &portfolio,
                            cash_policy: &cash,
                            safety: crate::simulation_runtime::AmendmentSafety {
                                session: &session,
                                risk_policy: &risk,
                                bands: None
                            },
                            latency_ns: 0,
                        }
                    )
                    .is_err());
            }
        }
        for write in &writes {
            let receipt = write.receipt().unwrap();
            let decision = receipt.decision();
            match &decision.actions[0] {
                Action::Enter(_) => {
                    let plan = decision_orders::bracket(
                        receipt,
                        0,
                        &decision_orders::Allocation {
                            account: decision.scope.account.clone(),
                            instrument: 1,
                            quantity: if decision.scope.account == "a" { 1 } else { 2 },
                            price_scale: 2,
                            tick: 1,
                            entry_limit: 1001,
                            deadline_ns: 202_000_000_000,
                        },
                        now,
                        false,
                        None,
                        &risk,
                    )
                    .unwrap();
                    let allocation = decision_orders::Allocation {
                        account: plan.bracket.account.clone(),
                        instrument: plan.bracket.instrument,
                        quantity: plan.bracket.quantity,
                        price_scale: plan.bracket.price_scale,
                        tick: plan.bracket.tick,
                        entry_limit: plan.bracket.entry,
                        deadline_ns: plan.bracket.deadline_ns,
                    };
                    let request = |allocation, latency_ns| crate::playback_runtime::EntryRequest {
                        allocation,
                        portfolio: &portfolio,
                        cash_policy: &cash,
                        safety: crate::simulation_runtime::AmendmentSafety {
                            session: &session,
                            risk_policy: &risk,
                            bands: None,
                        },
                        latency_ns,
                    };
                    let before = portfolio
                        .snapshot(&decision.scope.account)
                        .unwrap()
                        .reservations;
                    if fail_submission {
                        // Valid economic geometry but a scale different from the
                        // simulator instrument. Submission fails after reservation.
                        let mut bad = allocation.clone();
                        bad.price_scale = 3;
                        bad.entry_limit *= 10;
                        assert!(controller
                            .enter_action(&decision.decision_id, 0, request(&bad, 0))
                            .is_err());
                        let reserved = portfolio
                            .snapshot(&decision.scope.account)
                            .unwrap()
                            .reservations;
                        assert_eq!(reserved.len(), before.len() + 1);
                        assert!(reserved.contains_key(&plan.bracket.command_id));
                        assert!(controller
                            .enter_action(&decision.decision_id, 0, request(&bad, 0))
                            .is_err());
                        assert!(controller
                            .enter_action(&decision.decision_id, 0, request(&allocation, 0))
                            .is_err());
                        assert_eq!(
                            portfolio
                                .snapshot(&decision.scope.account)
                                .unwrap()
                                .reservations,
                            reserved
                        );
                        assert!(!controller.pending_actions().is_empty());
                        assert!(controller.acknowledge().is_err());
                        return;
                    }
                    assert!(controller
                        .enter_action(&decision.decision_id, 0, request(&allocation, 1))
                        .is_err());
                    assert_eq!(
                        portfolio
                            .snapshot(&decision.scope.account)
                            .unwrap()
                            .reservations,
                        before
                    );
                    assert!(controller
                        .enter_action("unknown", 0, request(&allocation, 0))
                        .is_err());
                    for _ in 0..2 {
                        let submitted = controller
                            .enter_action(&decision.decision_id, 0, request(&allocation, 0))
                            .unwrap();
                        assert_eq!(
                            content_hash(&submitted).unwrap(),
                            content_hash(&plan).unwrap()
                        );
                    }
                    let mut changed = allocation.clone();
                    changed.quantity += 1;
                    let before = portfolio
                        .snapshot(&decision.scope.account)
                        .unwrap()
                        .reservations;
                    assert!(controller
                        .enter_action(&decision.decision_id, 0, request(&changed, 0))
                        .is_err());
                    assert_eq!(
                        portfolio
                            .snapshot(&decision.scope.account)
                            .unwrap()
                            .reservations,
                        before
                    );
                    entries += 1;
                    commands.push(plan.bracket.command_id.clone());
                }
                Action::ReplaceTarget(_) => {
                    for _ in 0..2 {
                        controller
                            .protection_action(
                                &decision.decision_id,
                                0,
                                crate::simulation_runtime::AmendmentSafety {
                                    session: &session,
                                    risk_policy: &risk,
                                    bands: None,
                                },
                            )
                            .unwrap();
                    }
                }
                Action::Exit { .. } => {
                    for _ in 0..2 {
                        controller.exit_action(&decision.decision_id, 0).unwrap();
                    }
                    exits += 1;
                }
                Action::CancelEntry { .. } => {
                    controller
                        .cancel_entry_action(&decision.decision_id, 0)
                        .unwrap();
                }
                _ => {}
            }
        }
        if cancel_unfilled && !quote && quotes == 1 {
            let outcomes = controller
                .reconcile_funding(&portfolio, &BTreeMap::new(), 2, 10)
                .unwrap();
            assert_eq!(outcomes.len(), 2);
            for outcome in outcomes {
                assert_eq!(
                    outcome.kind,
                    crate::simulation_runtime::funding::Kind::ReleaseUnfilled
                );
                assert!(outcome.result.unwrap());
            }
            for command in &commands {
                assert!(!controller
                    .release_unfilled_reservation(command, &portfolio)
                    .unwrap());
            }
            released = true;
        }
        assert!(controller.pending_actions().is_empty());
        let mut last_fills: BTreeMap<String, arte_core::execution_events::Fill> = BTreeMap::new();
        for row in fills.rows.values() {
            let fill: arte_core::execution_events::Fill = serde_json::from_str(row).unwrap();
            if last_fills
                .get(&fill.command_id)
                .is_none_or(|previous| previous.sequence < fill.sequence)
            {
                last_fills.insert(fill.command_id.clone(), fill);
            }
        }
        let cut = arte_core::portfolio::checkpoint::Cut {
            boundary_sequence: input.source_sequence,
            boundary_hash: input.event_id.clone(),
            at_ns: now,
        };
        let limits = crate::simulation_runtime::checkpoint::Limits {
            maximum_bytes: 1_000_000,
            maximum_orders: 4,
            maximum_pending_fills: 8,
            projection: arte_core::execution_positions::checkpoint::Limits {
                positions: 2,
                fills: 100,
                lots_per_position: 8,
                bytes: 100_000,
            },
        };
        let mut image = controller
            .checkpoint(&manifest, &cut, &last_fills, limits, 2_000_000)
            .unwrap();
        let root: serde_json::Value = serde_json::from_slice(&image.root.payload).unwrap();
        assert_eq!(root["version"], 5);
        assert_eq!(root["targets"].as_object().unwrap().len(), 2);
        let context =
            content_hash(&("arte.playback-controller-cut.v1", manifest.hash(), &cut)).unwrap();
        let receipts: Vec<_> = writes
            .iter()
            .map(|write| write.receipt().unwrap())
            .collect();
        let restore = |bundle: &crate::playback_runtime::checkpoint::Bundle| {
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
                    maximum_bytes: 2_000_000,
                },
                1,
                2,
                &receipts,
                arte_core::simulation_costs::Pinned::new(cost_model.clone(), &manifest).unwrap(),
                limits,
                2_000_000,
            )
        };
        let restored = restore(&image).unwrap();
        assert_eq!(
            restored
                .checkpoint(&manifest, &cut, &last_fills, limits, 2_000_000)
                .unwrap()
                .root
                .id,
            image.root.id
        );
        for receipt in &receipts {
            let scope = &receipt.decision().scope;
            if !matches!(
                receipt.decision().actions[0],
                Action::Hold { .. } | Action::Wait { .. }
            ) {
                assert_eq!(
                    content_hash(
                        &restored
                            .retained_entry_allocation(&receipt.decision().decision_id, 0)
                            .unwrap()
                    )
                    .unwrap(),
                    content_hash(
                        &controller
                            .retained_entry_allocation(&receipt.decision().decision_id, 0)
                            .unwrap()
                    )
                    .unwrap()
                );
            }
            assert_eq!(
                content_hash(&restored.owned_candidate_position(scope).unwrap().position).unwrap(),
                content_hash(&controller.owned_candidate_position(scope).unwrap().position)
                    .unwrap()
            );
        }
        let original = image.root.clone();
        let text = String::from_utf8(original.payload.clone()).unwrap();
        if let Some(saved) = root["actions"]
            .as_array()
            .unwrap()
            .iter()
            .find(|a| a["allocation"].is_object())
        {
            let quantity = saved["allocation"]["quantity"].as_u64().unwrap();
            let changed = text.replacen(
                &format!("\"quantity\":{quantity},"),
                &format!("\"quantity\":{},", quantity + 1),
                1,
            );
            assert_ne!(changed, text);
            image.root = arte_core::seed_storage::Object::new(changed.into_bytes());
            assert!(restore(&image).is_err());
            image.root = original.clone();
        }
        // Preserve canonical field order while changing one target clock.
        let record = root["targets"]
            .as_object()
            .unwrap()
            .values()
            .next()
            .unwrap();
        let decision_hash = record["decision_hash"].as_str().unwrap();
        let target_at = record["at_ns"].as_u64().unwrap();
        let changed = text.replacen(
            &format!("\"decision_hash\":\"{decision_hash}\",\"at_ns\":{target_at}"),
            &format!(
                "\"decision_hash\":\"{decision_hash}\",\"at_ns\":{}",
                now + 1
            ),
            1,
        );
        assert_ne!(changed, text);
        image.root = arte_core::seed_storage::Object::new(changed.into_bytes());
        assert!(restore(&image).is_err());
        image.root = original;
        controller = restored;
        controller
            .require_portfolio(
                &portfolio,
                &std::collections::BTreeMap::from([(1, currency.clone())]),
            )
            .unwrap();
        controller.acknowledge().unwrap();
        controller.resume().unwrap();
    }
    assert!(completed);
    if !cancel_unfilled {
        let without_receipts = Portfolio::new(
            ["a", "b"]
                .into_iter()
                .map(|id| (id.into(), portfolio.snapshot(id).unwrap()))
                .collect(),
        )
        .unwrap();
        assert!(controller
            .require_portfolio(
                &without_receipts,
                &std::collections::BTreeMap::from([(1, currency.clone())])
            )
            .is_err());
        assert!(controller
            .require_portfolio(&portfolio, &std::collections::BTreeMap::new())
            .is_err());
    }
    assert_eq!(
        (quotes, entries, exits),
        (4, 2, if target_exit || cancel_unfilled { 0 } else { 2 })
    );
    for (account, quantity) in [("a", 1), ("b", 2)] {
        if cancel_unfilled {
            assert!(controller.position(&position_key(account)).is_none());
            let state = portfolio.snapshot(account).unwrap();
            assert!(state.reservations.is_empty());
            assert_eq!(
                state.broker_available_minor,
                if account == "a" { 2000 } else { 4000 }
            );
            continue;
        }
        let position = controller.position(&position_key(account)).unwrap();
        assert_eq!(position.quantity, 0);
        assert_eq!(
            position.realized_gross_pnl_atoms,
            (if target_exit { 149 } else { -2 }) * quantity
        );
        let state = portfolio.snapshot(account).unwrap();
        assert!(state.reservations.is_empty());
        assert_eq!(
            i128::from(state.broker_available_minor),
            (if account == "a" { 2000 } else { 4000 })
                + (if target_exit { 149 } else { -2 }) * quantity
                - 4
        );
    }
    assert_eq!(fills.rows.len(), if cancel_unfilled { 0 } else { 4 });
    for (index, command) in commands.iter().enumerate() {
        assert_eq!(
            controller.fees_minor(command).unwrap(),
            if cancel_unfilled { None } else { Some(4) }
        );
        if cancel_unfilled {
            assert!(controller.closed_net_cash_minor(command).is_err());
        } else {
            assert_eq!(
                controller.closed_net_cash_minor(command).unwrap(),
                (if target_exit { 149 } else { -2 }) * (index as i128 + 1) - 4
            );
        }
    }
}

fn position_key(account: &str) -> Key {
    Key {
        origin_hash: content_hash(&("simulated-position-v1", "run", MODEL)).unwrap(),
        account: account.into(),
        instrument: 1,
    }
}
fn proposal(now: u64) -> arte_core::strategy_entry::Proposal {
    arte_core::strategy_entry::Proposal {
        reason: "execution-fixture".into(),
        stop: 9.,
        target: 11.,
        maximum_buy_price: 10.01,
        target_selection: None,
        boundary: arte_core::strategy_targets::TargetLevel {
            geometry: arte_core::strategy_encounters::Level {
                id: "boundary".into(),
                price: 10.,
                lower: 9.9,
                upper: 10.1,
                role: arte_core::v7_encounters::ActiveRole::Resistance,
                confirmed_at_ns: now - 1,
            },
            historical: true,
            transition_from: None,
            synthetic: false,
        },
        swing: arte_core::strategy_targets::Swing {
            id: "swing".into(),
            lower: 9.,
            price: 9.,
            upper: 9.1,
            pivot_at_ns: now - 2,
            confirmed_at_ns: now - 1,
            support: true,
            active: true,
        },
        setup: arte_core::strategy_lifecycle::PositionSetup {
            confirmed_at_ns: now,
            phase: arte_core::strategy_lifecycle::Phase::Building,
            breakout_threshold: 10.1,
            breakout_at_ns: None,
            initial_fill_price: None,
            initial_risk: None,
            best_close: 10.,
            entry_failure_recovery: None,
            entry_bar: arte_core::market::Bar {
                start_ns: now - 1_000_000_000,
                end_ns: now,
                open: 10.,
                high: 10.1,
                low: 9.9,
                close: 10.,
                volume: 1.,
                notional: 10.,
                trades: 1,
            },
        },
    }
}
