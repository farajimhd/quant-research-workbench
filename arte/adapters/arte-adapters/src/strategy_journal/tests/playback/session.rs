use super::*;
use crate::playback_runtime::session::{Limits, Request, Session};
use arte_core::portfolio::Account;
use std::collections::BTreeMap;

fn request<'a>(
    run: &Run,
    manifest: &'a Pinned,
    costs: &arte_core::simulation_costs::Pinned,
) -> Request<'a> {
    Request {
        manifest,
        configurations: run
            .scopes()
            .iter()
            .map(|scope| {
                (
                    arte_core::content_hash(scope).unwrap(),
                    policies::config(&scope.account),
                )
            })
            .collect(),
        accounts: run
            .scopes()
            .iter()
            .map(|scope| {
                (
                    scope.account.clone(),
                    Account {
                        currency: "USD".into(),
                        currency_scale: 2,
                        simulation_run_id: Some("run".into()),
                        budget_minor: if scope.account == "a" { 10000 } else { 20000 },
                        broker_available_minor: 15000,
                        balance_at_ns: 200_000_000_000,
                        max_balance_age_ns: 10_000_000_000,
                        reservations: BTreeMap::new(),
                    },
                )
            })
            .collect(),
        price_scale: 2,
        fill_model: crate::test_fill_model(),
        cost_model: costs.model().clone(),
        limits: Limits {
            maximum_orders: 4,
            maximum_positions: 2,
            maximum_fills: 100,
            maximum_lots_per_position: 8,
            maximum_pending_fills: 8,
            maximum_candidate_state_bytes: 100_000,
        },
    }
}

#[test]
fn assembled_session_is_paused_and_preserves_separate_account_budgets() {
    let (run, costs, manifest, _) = run_candidate_fixture(true, false, false, true);
    let input = request(&run, &manifest, &costs);
    let mut session = Session::new(run, input).unwrap();
    assert_eq!(
        session.controller.status().mode,
        arte_core::market_structure::scheduler::playback::Mode::Paused
    );
    assert_eq!(session.controller.status().admitted_events, 0);
    assert_eq!(session.portfolio.snapshot("a").unwrap().budget_minor, 10000);
    assert_eq!(session.portfolio.snapshot("b").unwrap().budget_minor, 20000);
    assert_eq!(session.candidates.scope_hashes().count(), 2);
    session.controller.resume().unwrap();
    assert_eq!(session.controller.poll().unwrap(), Poll::Boundary);
    assert_eq!(session.controller.execution_status().pending_fills, 0);
    assert!(session.controller.acknowledge().is_err());
    session.candidates.observe(&session.controller).unwrap();
    assert_eq!(
        session
            .candidates
            .needed_evaluations(&session.controller)
            .unwrap()
            .len(),
        2
    );
}

#[test]
fn assembled_session_rejects_incompatible_inputs_before_use() {
    for fault in 0..16 {
        let (mut run, costs, manifest, _) = run_candidate_fixture(true, false, false, true);
        let mut input = request(&run, &manifest, &costs);
        match fault {
            0 => {
                input.accounts.remove("a");
            }
            1 => {
                input
                    .accounts
                    .insert("foreign".into(), input.accounts["a"].clone());
            }
            2 => {
                input.accounts.get_mut("a").unwrap().simulation_run_id = None;
            }
            3 => {
                input.accounts.get_mut("a").unwrap().currency = "CAD".into();
            }
            4 => {
                input.accounts.get_mut("a").unwrap().reservations.insert(
                    "old".into(),
                    arte_core::portfolio::Reservation {
                        command_id: "old".into(),
                        instrument: 1,
                        cash_minor: 1,
                    },
                );
            }
            5 => {
                input.limits.maximum_positions = 1;
            }
            6 => {
                input.limits.maximum_pending_fills = 1;
            }
            7 => {
                input.fill_model.maximum_quote_age_ns += 1;
            }
            8 => {
                input.configurations.clear();
            }
            9 => {
                run.resume().unwrap();
            }
            10 => {
                run.resume().unwrap();
                assert_eq!(run.poll().unwrap(), Poll::Boundary);
                run.pause().unwrap();
            }
            11 => {
                input.cost_model.fixed_per_fill_minor += 1;
            }
            12 => {
                input.configurations.pop_first();
            }
            13 => {
                input.accounts.get_mut("a").unwrap().currency_scale = 3;
            }
            14 => {
                input.limits.maximum_candidate_state_bytes = 0;
            }
            15 => {
                input.price_scale = 10;
            }
            _ => unreachable!(),
        }
        assert!(Session::new(run, input).is_err(), "fault {fault} passed");
    }
}

#[test]
fn assembled_strategies_share_one_account_reservation_authority() {
    let (run, costs, manifest, _) = run_configured_fixture(true, false, false, true, true);
    let input = request(&run, &manifest, &costs);
    let session = Session::new(run, input).unwrap();
    assert_eq!(session.candidates.scope_hashes().count(), 3);
    let reservation = |command: &str, cash| arte_core::portfolio::Reservation {
        command_id: command.into(),
        instrument: 1,
        cash_minor: cash,
    };
    // Funding-authority check only; these do not submit simulator orders.
    session
        .portfolio
        .reserve("a", reservation("s", 7000), 200_000_000_000)
        .unwrap();
    assert!(session
        .portfolio
        .reserve("a", reservation("t", 4000), 200_000_000_000)
        .is_err());
    session
        .portfolio
        .reserve("b", reservation("s-b", 14000), 200_000_000_000)
        .unwrap();
    assert_eq!(
        session.portfolio.snapshot("a").unwrap().reservations.len(),
        1
    );
    assert_eq!(
        session.portfolio.snapshot("b").unwrap().reservations.len(),
        1
    );
}
