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
    lifecycle(false, false).await;
}
#[tokio::test]
async fn replacement_target_controls_later_fills_not_the_original_target() {
    lifecycle(true, false).await;
}
#[tokio::test]
async fn cancelled_unfilled_orders_release_exact_funding_without_fabricated_cash() {
    lifecycle(false, true).await;
}
async fn lifecycle(target_exit: bool, cancel_unfilled: bool) {
    let (run, costs) = run_with_costs(true, true, target_exit);
    let mut runtimes: Vec<_> = run
        .scopes()
        .iter()
        .map(|scope| {
            arte_core::strategy_transaction::Runtime::new(scope.clone(), 0_u64, 1024).unwrap()
        })
        .collect();
    let mut execution = crate::simulation_runtime::Runtime::new(
        Simulator::new_scoped("run", 1, 2, 4, 10000).unwrap(),
        Projection::new(2, 20, 8).unwrap(),
        8,
    )
    .unwrap();
    execution
        .bind_source(run.market().unwrap().source_scope())
        .unwrap();
    let mut controller =
        crate::playback_runtime::Runtime::new(run, execution, 2_000_000_000, costs).unwrap();
    let portfolio = Portfolio::new(
        [("a", 2000), ("b", 4000)]
            .into_iter()
            .map(|(id, budget)| {
                (
                    id.into(),
                    Account {
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
    let mut fills = Fills {
        fail: true,
        rows: BTreeMap::new(),
    };
    let mut stores = [Decisions::default(), Decisions::default()];
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
            assert!(controller.acknowledge().is_err());
            for command in &commands {
                assert!(controller
                    .release_unfilled_reservation(command, &portfolio)
                    .is_err());
            }
            if fills.fail {
                assert!(controller.commit_fills(&mut fills).await.is_err());
                assert!(controller.decision_view().is_err());
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
            let quantity = controller.position(&key).map_or(0, |p| p.quantity);
            let mut safety = prepared_account(account)
                .pending_decision()
                .unwrap()
                .safety
                .clone();
            safety.position_quantity = quantity;
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
                    let funding =
                        order_funding::reserve(&portfolio, &plan, &cash, now, false, None, &risk)
                            .unwrap();
                    for _ in 0..2 {
                        controller
                            .submit_reserved(crate::simulation_runtime::Submission {
                                plan: &plan,
                                funding: &funding,
                                portfolio: &portfolio,
                                cash_policy: &cash,
                                risk_policy: &risk,
                                bands: None,
                                session: &session,
                                now_ns: now,
                                latency_ns: 0,
                            })
                            .unwrap();
                    }
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
            for command in &commands {
                assert!(controller
                    .release_unfilled_reservation(command, &portfolio)
                    .unwrap());
                assert!(!controller
                    .release_unfilled_reservation(command, &portfolio)
                    .unwrap());
            }
            released = true;
        }
        assert!(controller.pending_actions().is_empty());
        controller.acknowledge().unwrap();
    }
    assert!(completed);
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
    }
    assert_eq!(fills.rows.len(), if cancel_unfilled { 0 } else { 4 });
    for command in &commands {
        assert_eq!(
            controller.fees_minor(command).unwrap(),
            if cancel_unfilled { None } else { Some(4) }
        );
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
