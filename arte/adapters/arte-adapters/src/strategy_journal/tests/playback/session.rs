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
    let document = crate::playback_runtime::session::document::Document::from_request(&input);
    let hash = document.hash().unwrap();
    let bytes = serde_json::to_vec(&document).unwrap();
    let loaded =
        crate::playback_runtime::session::document::Document::read(bytes.as_slice(), &hash)
            .unwrap();
    let mut session = Session::from_document(run, &manifest, loaded, &hash).unwrap();
    assert_eq!(session.startup_hash(), hash);
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

#[tokio::test]
async fn startup_document_pins_balances_limits_models_and_manifest() {
    use crate::playback_runtime::session::document::Document;
    let (run, costs, manifest, _) = run_candidate_fixture(true, false, false, true);
    let document = Document::from_request(&request(&run, &manifest, &costs));
    crate::clickhouse::startup_roundtrip_test(&manifest, &document).await;
    // Transport stress only: extra accounts intentionally do not match consumers.
    // Storage identity must not be confused with semantic session acceptance.
    let mut large = document.clone();
    let account = large.accounts["a"].clone();
    for index in 0..4094 {
        large.accounts.insert(
            format!("account-{index:04}-{}", "x".repeat(100)),
            account.clone(),
        );
    }
    assert!(serde_json::to_vec(&large).unwrap().len() > 1024 * 1024);
    crate::clickhouse::startup_roundtrip_test(&manifest, &large).await;
    let hash = document.hash().unwrap();
    for change in 0..7 {
        let mut changed = document.clone();
        match change {
            0 => changed.accounts.get_mut("a").unwrap().budget_minor += 1,
            1 => {
                changed
                    .accounts
                    .get_mut("b")
                    .unwrap()
                    .broker_available_minor += 1
            }
            2 => changed.limits.maximum_orders += 1,
            3 => changed.price_scale += 1,
            4 => changed.fill_model.submission_latency_ns += 1,
            5 => changed.cost_model.fixed_per_fill_minor += 1,
            6 => changed.manifest_hash = "f".repeat(64),
            _ => unreachable!(),
        }
        assert_ne!(changed.hash().unwrap(), hash);
        assert!(Document::decode(&serde_json::to_vec(&changed).unwrap(), &hash).is_err());
    }
    let mut unknown = serde_json::to_value(&document).unwrap();
    unknown["unexpected"] = true.into();
    assert!(Document::decode(&serde_json::to_vec(&unknown).unwrap(), &hash).is_err());
    let mut wrong_version = document.clone();
    wrong_version.schema_version = 2;
    assert!(wrong_version.hash().is_err());
    assert!(Document::decode(b"{}", &hash).is_err());
    let account = serde_json::to_string(&document.accounts["a"]).unwrap();
    let text = serde_json::to_string(&document).unwrap();
    let duplicate = text.replace(
        "\"accounts\":{",
        &format!("\"accounts\":{{\"a\":{account},"),
    );
    assert!(Document::decode(duplicate.as_bytes(), &hash).is_err());
    assert!(Document::read(std::io::repeat(b' '), &hash).is_err());
    assert!(Session::from_document(run, &manifest, document, &"0".repeat(64)).is_err());
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
