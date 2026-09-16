//! Network-free coordinator exercise using explicit fixture inputs, not defaults.
use crate::playback_runtime::{
    self,
    candidates::Candidates,
    runner::{Inputs, Journals, Step},
};
use arte_core::{Error, Result};
use std::collections::BTreeMap;
struct NoFills;
impl crate::fill_journal::Publisher for NoFills {
    async fn publish(
        &mut self,
        _: &crate::fill_journal::Batch,
    ) -> Result<BTreeMap<String, String>> {
        Err(Error::Unready("unexpected fixture fill".into()))
    }
}
struct NoRejections;
impl crate::rejection_journal::Publisher for NoRejections {
    async fn append(
        &mut self,
        _: &arte_core::strategy_transaction::Committed,
        _: &arte_core::action_rejection::Record,
    ) -> Result<arte_core::action_rejection::Record> {
        Err(Error::Unready("unexpected fixture rejection".into()))
    }
}
pub(super) async fn service<P: crate::strategy_journal::Publisher>(
    controller: &mut playback_runtime::Runtime,
    candidates: &mut Candidates,
    portfolio: &arte_core::portfolio::Portfolio,
    decisions: &mut BTreeMap<String, P>,
) -> Step {
    service_with(
        controller,
        candidates,
        portfolio,
        decisions,
        &mut BTreeMap::<String, NoFills>::new(),
    )
    .await
    .unwrap()
}
async fn service_with<P: crate::strategy_journal::Publisher, F: crate::fill_journal::Publisher>(
    controller: &mut playback_runtime::Runtime,
    candidates: &mut Candidates,
    portfolio: &arte_core::portfolio::Portfolio,
    decisions: &mut BTreeMap<String, P>,
    fills: &mut BTreeMap<String, F>,
) -> Result<Step> {
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
    controller
        .service_boundary(
            candidates,
            Inputs {
                actions: playback_runtime::SizedActionInputs {
                    sizing: &BTreeMap::new(),
                    cash_policies: &BTreeMap::new(),
                    portfolio,
                    safety: crate::simulation_runtime::AmendmentSafety {
                        session: &session,
                        risk_policy: &risk,
                        bands: None,
                    },
                    latency_ns: 0,
                    maximum_actions: 2,
                },
                currencies: &BTreeMap::new(),
                maximum_funding_orders: 2,
                maximum_settlement_receipts: 100,
                decision_concurrency: 2,
            },
            Journals {
                fills,
                decisions,
                rejections: &mut NoRejections,
            },
        )
        .await
}

#[tokio::test]
async fn coordinator_commits_each_fill_scope_before_exposing_candidate_evaluation() {
    coordinator_fill_fixture(false).await;
}
#[tokio::test]
async fn coordinator_does_not_skip_unreconciled_terminal_funding() {
    coordinator_fill_fixture(true).await;
}
async fn coordinator_fill_fixture(expire: bool) {
    use arte_core::{
        execution_positions::Projection,
        orders::{Bracket, Side},
        portfolio::{Account, Portfolio},
        simulated_execution::Simulator,
    };
    let (run, costs, manifest, _) = super::run_candidate_fixture(true, false, false, true);
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
        playback_runtime::Runtime::new(run, execution, crate::test_fill_model(), costs).unwrap();
    let mut candidates = Candidates::new(
        &controller,
        &manifest,
        super::policies::config("a").features,
        100_000,
    )
    .unwrap();
    let mut decisions: BTreeMap<_, _> = candidates
        .scope_hashes()
        .map(|key| (key.to_owned(), super::timed(0, false)))
        .collect();
    let portfolio = Portfolio::new(
        ["a", "b"]
            .into_iter()
            .map(|account| {
                (
                    account.into(),
                    Account {
                        currency: "USD".into(),
                        currency_scale: 2,
                        simulation_run_id: Some("run".into()),
                        budget_minor: 10000,
                        broker_available_minor: 10000,
                        balance_at_ns: 200_000_000_000,
                        max_balance_age_ns: 10_000_000_000,
                        reservations: BTreeMap::new(),
                    },
                )
            })
            .collect(),
    )
    .unwrap();
    // Raw seeded orders are confined to this plumbing fixture; production entry
    // funding and strategy approval are covered by the lifecycle fixtures.
    for account in ["a", "b"] {
        controller
            .seed_test_order(
                Bracket {
                    command_id: format!("fill-{account}"),
                    account: account.into(),
                    instrument: 1,
                    side: Side::Long,
                    quantity: 1,
                    entry: 1001,
                    price_scale: 2,
                    stop: Some(900),
                    target: Some(1100),
                    tick: 1,
                    deadline_ns: if expire {
                        200_500_000_000
                    } else {
                        202_000_000_000
                    },
                },
                200_000_000_000,
            )
            .unwrap();
    }
    struct Store {
        fail: bool,
        pause: bool,
        calls: usize,
        rows: BTreeMap<String, String>,
    }
    impl crate::fill_journal::Publisher for Store {
        async fn publish(
            &mut self,
            batch: &crate::fill_journal::Batch,
        ) -> Result<BTreeMap<String, String>> {
            self.calls += 1;
            self.rows.extend(batch.rows().clone());
            if std::mem::take(&mut self.fail) {
                return Err(Error::Unready("ambiguous fixture fill write".into()));
            }
            if self.pause {
                std::future::pending::<()>().await;
            }
            Ok(batch.rows().clone())
        }
    }
    let mut fills = BTreeMap::<String, Store>::new();
    controller.resume().unwrap();
    assert_eq!(controller.poll().unwrap(), super::Poll::Boundary);
    if expire {
        assert_eq!(controller.execution_status().pending_fills, 0);
        for _ in 0..2 {
            let Step::Funding(outcomes) = service_with(
                &mut controller,
                &mut candidates,
                &portfolio,
                &mut decisions,
                &mut fills,
            )
            .await
            .unwrap() else {
                panic!("unreconciled terminal orders must block evaluation");
            };
            assert_eq!(outcomes.len(), 2);
            assert!(outcomes.iter().all(|outcome| outcome.result.is_err()
                && outcome.kind == crate::simulation_runtime::funding::Kind::ReleaseUnfilled));
            assert!(candidates.features().snapshot().unwrap().is_none());
            assert!(controller.acknowledge().is_err());
            assert_eq!(controller.status().acknowledged_boundaries, 0);
        }
        for account in ["a", "b"] {
            assert_eq!(portfolio.snapshot(account).unwrap().budget_minor, 10000);
            assert!(portfolio.snapshot(account).unwrap().reservations.is_empty());
        }
        return;
    }
    assert_eq!(controller.execution_status().pending_fills, 2);
    assert!(candidates.features().snapshot().unwrap().is_none());
    for remaining in [2, 1] {
        let scope = controller.next_fill_scope_hash().unwrap().unwrap();
        assert!(service_with(
            &mut controller,
            &mut candidates,
            &portfolio,
            &mut decisions,
            &mut fills
        )
        .await
        .is_err());
        assert_eq!(controller.execution_status().pending_fills, remaining);
        fills.insert(
            scope.clone(),
            Store {
                fail: true,
                pause: remaining == 2,
                calls: 0,
                rows: BTreeMap::new(),
            },
        );
        assert!(service_with(
            &mut controller,
            &mut candidates,
            &portfolio,
            &mut decisions,
            &mut fills
        )
        .await
        .is_err());
        assert_eq!(controller.execution_status().pending_fills, remaining);
        assert!(controller.decision_view().is_err());
        assert!(controller.acknowledge().is_err());
        if remaining == 2 {
            let mut cancelled = Box::pin(service_with(
                &mut controller,
                &mut candidates,
                &portfolio,
                &mut decisions,
                &mut fills,
            ));
            assert!(std::future::Future::poll(
                cancelled.as_mut(),
                &mut std::task::Context::from_waker(std::task::Waker::noop())
            )
            .is_pending());
            drop(cancelled);
            assert_eq!(controller.execution_status().pending_fills, 2);
            assert!(candidates.features().snapshot().unwrap().is_none());
            fills.get_mut(&scope).unwrap().pause = false;
        }
        let Step::FillsPublished { scope_hash } = service_with(
            &mut controller,
            &mut candidates,
            &portfolio,
            &mut decisions,
            &mut fills,
        )
        .await
        .unwrap() else {
            panic!("fills must publish before evaluation");
        };
        assert_eq!(scope_hash, scope);
        assert_eq!(fills[&scope].calls, if remaining == 2 { 3 } else { 2 });
        assert_eq!(fills[&scope].rows.len(), 1);
        assert!(candidates.features().snapshot().unwrap().is_none());
    }
    assert_eq!(controller.execution_status().pending_fills, 0);
    assert!(controller.next_fill_scope_hash().unwrap().is_none());
    for account in ["a", "b"] {
        let fill = fills
            .values()
            .flat_map(|store| store.rows.values())
            .map(|row| serde_json::from_str::<arte_core::execution_events::Fill>(row).unwrap())
            .find(|fill| fill.account == account)
            .unwrap();
        let key = arte_core::execution_positions::Key::from_fill(&fill).unwrap();
        assert_eq!(controller.position(&key).unwrap().quantity, 1);
        // A plumbing-only seeded order has no originating strategy authorization.
        assert!(controller.account_view(account).is_err());
        assert_eq!(
            controller.fees_minor(&format!("fill-{account}")).unwrap(),
            Some(2)
        );
    }
    let Step::NeedsEvaluation { scope_hashes } = service_with(
        &mut controller,
        &mut candidates,
        &portfolio,
        &mut decisions,
        &mut fills,
    )
    .await
    .unwrap() else {
        panic!("evaluation begins only after every fill scope commits");
    };
    assert_eq!(scope_hashes.len(), 2);
    assert!(candidates.features().snapshot().unwrap().is_some());
    assert_eq!(controller.status().acknowledged_boundaries, 0);
    assert!(controller.acknowledge().is_err());
}
