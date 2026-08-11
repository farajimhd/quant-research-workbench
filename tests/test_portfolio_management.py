from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.trading_runtime.ibkr_schema import AccountLedger, AccountSummary, PortfolioPosition
from src.trading_runtime.control_plane import TradingControlPlane
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.order_management import OrderGroupSnapshot, OrderManagementState
from src.trading_runtime.portfolio import (
    PortfolioAccountProfile,
    PortfolioAllocationLot,
    PortfolioControlMode,
    PortfolioDecisionStatus,
    PortfolioGroupPolicy,
    PortfolioManagementEngine,
    PortfolioPolicy,
)
from src.trading_runtime.portfolio_config import (
    configured_portfolio_profiles,
    configured_portfolio_profiles_for_runtime,
    portfolio_profiles_from_configuration,
)
from src.trading_runtime.signals import CapitalRequest, StrategyIntent


NOW = datetime.now(timezone.utc)
RUNTIME_ROOT = Path(r"D:\TradingML\runtimes")


def summary(account_id: str, *, equity: float = 100_000, available: float = 80_000, at: datetime = NOW) -> AccountSummary:
    return AccountSummary(
        account_id=account_id,
        netliquidation=equity,
        totalcashvalue=available,
        buyingpower=available,
        grosspositionvalue=0,
        availablefunds=available,
        excessliquidity=available,
        timestamp=at,
    )


def ledger(account_id: str, *, cash: float = 80_000, at: datetime = NOW) -> AccountLedger:
    return AccountLedger(
        acctId=account_id,
        cashbalance=cash,
        settledcash=cash,
        stockmarketvalue=0,
        netliquidationvalue=100_000,
        realizedpnl=0,
        unrealizedpnl=0,
        timestamp=at,
    )


def position(account_id: str, ticker: str, quantity: float, price: float = 100) -> PortfolioPosition:
    return PortfolioPosition(
        acctId=account_id,
        conid=1,
        contractDesc=ticker,
        position=quantity,
        mktPrice=price,
        mktValue=quantity * price,
        avgCost=price,
        avgPrice=price,
        realizedPnl=0,
        unrealizedPnl=0,
        raw={"ticker": ticker},
    )


def intent(
    intent_id: str,
    *,
    action: str = "enter_long",
    ticker: str = "AAPL",
    quantity: float = 1_000,
    price: float = 100,
    invalidation: float | None = 98,
) -> StrategyIntent:
    return StrategyIntent(
        intent_id=intent_id,
        ticker=ticker,
        event_time=NOW,
        action=action,  # type: ignore[arg-type]
        quantity=quantity,
        reference_price=price,
        invalidation_price=invalidation,
        metadata={"assignment_id": f"assignment-{ticker}"},
    )


class PortfolioCausationTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_decision_links_strategy_intent_to_oms(self) -> None:
        journal = TradingJournal(Path(":memory:"))
        try:
            profile = PortfolioAccountProfile(
                "cash", "CASH1", "live", "cash", PortfolioPolicy()
            )
            engine = PortfolioManagementEngine(
                [profile],
                journal=journal,
                run_id="portfolio-test",
                strategy_id="strategy-a",
                strategy_revision=2,
            )
            engine.synchronize_snapshot(
                "CASH1",
                summary=summary("CASH1"),
                ledger=ledger("CASH1"),
                positions=[],
            )
            request = replace(
                intent("causal-request", quantity=10),
                metadata={
                    "assignment_id": "assignment-AAPL",
                    "correlation_id": "run:assignment-AAPL",
                    "causation_id": "event:qmd-signal-41",
                },
            )

            decision, approved = await engine.approve(request, account_id="CASH1")

            assert approved is not None
            self.assertEqual(approved.metadata["correlation_id"], "run:assignment-AAPL")
            self.assertEqual(approved.metadata["causation_id"], decision.decision_id)
            records = journal.records("portfolio-test")
            decision_record = next(
                row for row in records if row.entity_id == decision.decision_id
            )
            reservation_record = next(
                row for row in records if row.entity_id == decision.reservation_id
            )
            self.assertEqual(decision_record.payload["causation_id"], request.intent_id)
            self.assertEqual(reservation_record.payload["causation_id"], decision.decision_id)
        finally:
            journal.close()


class PortfolioManagementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=RUNTIME_ROOT)
        self.journal = TradingJournal(Path(self.temp.name) / "portfolio.sqlite3")

    def tearDown(self) -> None:
        self.journal.close()
        self.temp.cleanup()

    def engine(
        self,
        profiles: list[PortfolioAccountProfile],
        *,
        groups: list[PortfolioGroupPolicy] | None = None,
    ) -> PortfolioManagementEngine:
        return PortfolioManagementEngine(
            profiles,
            journal=self.journal,
            run_id="portfolio-test",
            strategy_id="strategy-a",
            strategy_revision=2,
            groups=groups or [],
        )

    async def test_account_policies_size_the_same_request_independently(self) -> None:
        conservative = PortfolioPolicy(
            policy_id="cash",
            maximum_position_fraction=0.05,
            maximum_ticker_fraction=0.05,
            maximum_planned_risk_fraction=0.005,
        )
        growth = PortfolioPolicy(
            policy_id="margin",
            allow_margin=True,
            allow_short=True,
            maximum_position_fraction=1.0,
            maximum_ticker_fraction=1.0,
            maximum_planned_risk_fraction=0.02,
        )
        engine = self.engine(
            [
                PortfolioAccountProfile("cash", "CASH1", "live", "cash", conservative),
                PortfolioAccountProfile("margin", "MARGIN1", "live", "margin", growth),
            ]
        )
        engine.synchronize_snapshot("CASH1", summary=summary("CASH1"), ledger=ledger("CASH1"), positions=[])
        engine.synchronize_snapshot(
            "MARGIN1",
            summary=summary("MARGIN1", available=200_000),
            ledger=ledger("MARGIN1", cash=200_000),
            positions=[],
        )

        cash_decision, cash_intent = await engine.approve(intent("cash-request"), account_id="CASH1")
        margin_decision, margin_intent = await engine.approve(intent("margin-request"), account_id="MARGIN1")

        self.assertEqual(cash_decision.status, PortfolioDecisionStatus.RESIZED)
        self.assertEqual(cash_intent.quantity, 50)
        self.assertEqual(margin_decision.status, PortfolioDecisionStatus.APPROVED)
        self.assertEqual(margin_intent.quantity, 1_000)

    async def test_account_guardrail_latch_blocks_other_strategy_runs(self) -> None:
        profile = PortfolioAccountProfile("cash", "CASH1", "live", "cash", PortfolioPolicy())
        plane = TradingControlPlane()
        first = PortfolioManagementEngine(
            [profile], journal=self.journal, run_id="run-a", strategy_id="strategy-a",
            strategy_revision=1, control_plane=plane,
        )
        second = PortfolioManagementEngine(
            [profile], journal=self.journal, run_id="run-b", strategy_id="strategy-b",
            strategy_revision=1, control_plane=plane,
        )
        for engine in (first, second):
            engine.synchronize_snapshot(
                "CASH1", summary=summary("CASH1"), ledger=ledger("CASH1"), positions=[]
            )
        first.set_control("cash", PortfolioControlMode.ENTRIES_PAUSED, reason="daily_loss")
        decision, approved = await second.approve(intent("cross-run-request"), account_id="CASH1")
        self.assertIsNone(approved)
        self.assertEqual(decision.status, PortfolioDecisionStatus.REJECTED)
        self.assertIn("entries_paused", decision.reasons)

    async def test_reservations_prevent_two_requests_from_spending_the_same_capacity(self) -> None:
        policy = PortfolioPolicy(
            policy_id="bounded",
            maximum_position_fraction=1,
            maximum_ticker_fraction=1,
            maximum_planned_risk_fraction=1,
            maximum_open_risk_fraction=1,
            maximum_order_notional=1_000_000,
        )
        engine = self.engine([PortfolioAccountProfile("cash", "CASH1", "live", "cash", policy)])
        engine.synchronize_snapshot(
            "CASH1",
            summary=summary("CASH1", equity=100_000, available=10_000),
            ledger=ledger("CASH1", cash=10_000),
            positions=[],
        )

        first, first_intent = await engine.approve(intent("first", quantity=80), account_id="CASH1")
        second, second_intent = await engine.approve(intent("second", quantity=80), account_id="CASH1")

        self.assertEqual(first.approved_quantity, 80)
        self.assertEqual(second.status, PortfolioDecisionStatus.RESIZED)
        self.assertEqual(second.approved_quantity, 20)
        self.assertIsNotNone(first_intent)
        self.assertIsNotNone(second_intent)

    async def test_separate_process_journals_share_fenced_account_capacity(self) -> None:
        policy = PortfolioPolicy(
            policy_id="cross-process",
            maximum_position_fraction=1,
            maximum_ticker_fraction=1,
            maximum_planned_risk_fraction=1,
            maximum_open_risk_fraction=1,
            maximum_order_notional=1_000_000,
        )
        profile = PortfolioAccountProfile("cash", "CASH1", "live", "cash", policy)
        second_journal = TradingJournal(Path(self.temp.name) / "portfolio.sqlite3")
        first = PortfolioManagementEngine(
            [profile],
            journal=self.journal,
            run_id="process-a",
            strategy_id="strategy-a",
            strategy_revision=1,
        )
        second = PortfolioManagementEngine(
            [profile],
            journal=second_journal,
            run_id="process-b",
            strategy_id="strategy-b",
            strategy_revision=1,
        )
        first.synchronize_snapshot(
            "CASH1",
            summary=summary("CASH1", equity=100_000, available=10_000),
            ledger=ledger("CASH1", cash=10_000),
            positions=[],
        )
        second.synchronize_snapshot(
            "CASH1",
            summary=summary("CASH1", equity=100_000, available=10_000),
            ledger=ledger("CASH1", cash=10_000),
            positions=[],
        )

        first_decision, _ = await first.approve(
            intent("process-a-request", quantity=80), account_id="CASH1"
        )
        second_decision, _ = await second.approve(
            intent("process-b-request", quantity=80), account_id="CASH1"
        )
        second_journal.close()

        self.assertEqual(first_decision.approved_quantity, 80)
        self.assertEqual(second_decision.approved_quantity, 20)
        reservation = second.reservations[second_decision.reservation_id]
        self.assertGreater(reservation.admission_epoch, 0)
        self.assertTrue(reservation.admission_owner.startswith("process-b:"))

    async def test_concurrent_cross_run_admission_cannot_overallocate_shared_account(self) -> None:
        policy = PortfolioPolicy(
            policy_id="concurrent",
            maximum_position_fraction=1,
            maximum_ticker_fraction=1,
            maximum_planned_risk_fraction=1,
            maximum_open_risk_fraction=1,
            maximum_order_notional=1_000_000,
        )
        profile = PortfolioAccountProfile("cash", "CASH1", "live", "cash", policy)
        second_journal = TradingJournal(Path(self.temp.name) / "portfolio.sqlite3")
        engines = [
            PortfolioManagementEngine(
                [profile],
                journal=journal,
                run_id=f"concurrent-{index}",
                strategy_id=f"strategy-{index}",
                strategy_revision=1,
            )
            for index, journal in enumerate((self.journal, second_journal), start=1)
        ]
        for engine in engines:
            engine.synchronize_snapshot(
                "CASH1",
                summary=summary("CASH1", equity=100_000, available=10_000),
                ledger=ledger("CASH1", cash=10_000),
                positions=[],
            )

        results = await asyncio.gather(
            engines[0].approve(intent("race-a", quantity=80), account_id="CASH1"),
            engines[1].approve(intent("race-b", quantity=80), account_id="CASH1"),
        )
        second_journal.close()

        approved = [decision.approved_quantity for decision, _ in results]
        self.assertEqual(sum(approved), 100)
        self.assertEqual(sorted(approved), [20, 80])

    def test_stale_lease_owner_cannot_release_newer_epoch(self) -> None:
        first = self.journal.acquire_portfolio_admission_lease(
            "portfolio-account:CASH1", owner_id="owner-a", ttl_seconds=0.001
        )
        self.assertIsNotNone(first)
        time.sleep(0.01)
        second = self.journal.acquire_portfolio_admission_lease(
            "portfolio-account:CASH1", owner_id="owner-b", ttl_seconds=30
        )
        self.assertIsNotNone(second)
        self.assertGreater(second["epoch"], first["epoch"])
        self.assertFalse(
            self.journal.release_portfolio_admission_lease(
                "portfolio-account:CASH1",
                owner_id="owner-a",
                epoch=first["epoch"],
            )
        )
        self.assertTrue(
            self.journal.portfolio_admission_lease_is_current(
                "portfolio-account:CASH1",
                owner_id="owner-b",
                epoch=second["epoch"],
            )
        )

    async def test_stale_live_state_blocks_entries_but_allows_broker_bounded_exit(self) -> None:
        old = NOW - timedelta(minutes=1)
        policy = PortfolioPolicy(policy_id="stale", maximum_snapshot_age_ms=100)
        engine = self.engine([PortfolioAccountProfile("cash", "CASH1", "live", "cash", policy)])
        engine.synchronize_snapshot(
            "CASH1",
            summary=summary("CASH1", at=old),
            ledger=ledger("CASH1", at=old),
            positions=[position("CASH1", "AAPL", 50)],
        )

        entry_decision, approved_entry = await engine.approve(intent("entry", quantity=10), account_id="CASH1")
        exit_decision, approved_exit = await engine.approve(
            intent("exit", action="exit", quantity=100),
            account_id="CASH1",
        )

        self.assertEqual(entry_decision.status, PortfolioDecisionStatus.REJECTED)
        self.assertIn("portfolio_snapshot_stale", entry_decision.reasons)
        self.assertEqual(exit_decision.status, PortfolioDecisionStatus.RESIZED)
        self.assertEqual(approved_exit.quantity, 50)
        self.assertIsNone(approved_entry)

    async def test_group_limit_is_enforced_across_accounts_without_rerouting(self) -> None:
        policy = PortfolioPolicy(
            policy_id="grouped",
            maximum_position_fraction=1,
            maximum_ticker_fraction=1,
            maximum_planned_risk_fraction=1,
            maximum_open_risk_fraction=1,
        )
        profiles = [
            PortfolioAccountProfile("one", "A1", "live", "margin", policy),
            PortfolioAccountProfile("two", "A2", "live", "margin", policy),
        ]
        group = PortfolioGroupPolicy("household", ("one", "two"), 15_000, 12_000)
        engine = self.engine(profiles, groups=[group])
        engine.synchronize_snapshot("A1", summary=summary("A1"), ledger=ledger("A1"), positions=[position("A1", "MSFT", 100)])
        engine.synchronize_snapshot("A2", summary=summary("A2"), ledger=ledger("A2"), positions=[])

        decision, approved = await engine.approve(
            intent("group", ticker="AAPL", quantity=100),
            account_id="A2",
        )

        self.assertEqual(decision.status, PortfolioDecisionStatus.RESIZED)
        self.assertEqual(approved.quantity, 50)
        self.assertEqual(decision.account_id, "A2")

    async def test_fill_converts_reservation_to_allocation_and_recovery_restores_it(self) -> None:
        policy = PortfolioPolicy(
            policy_id="fills",
            maximum_position_fraction=1,
            maximum_ticker_fraction=1,
            maximum_planned_risk_fraction=1,
            maximum_open_risk_fraction=1,
        )
        profile = PortfolioAccountProfile("margin", "M1", "paper", "margin", policy)
        engine = self.engine([profile])
        engine.synchronize_snapshot("M1", summary=summary("M1"), ledger=ledger("M1"), positions=[])
        decision, _ = await engine.approve(intent("fill-request", quantity=20), account_id="M1")
        engine.on_order_group_update(
            OrderGroupSnapshot(
                group_id="group",
                intent_id="fill-request",
                account_id="M1",
                ticker="AAPL",
                action="enter_long",
                state=OrderManagementState.FILLED,
                client_order_ids=("client",),
                broker_order_ids=("broker",),
                submitted_at=NOW,
                updated_at=NOW,
                filled_quantity=20,
                remaining_quantity=0,
                warning_message_ids=(),
                rejection_reason="",
                decision_to_submit_ms=1,
                policy_version=1,
                reentry_after_fill=False,
                assignment_id="assignment-AAPL",
            )
        )

        self.assertEqual(engine.reservations[decision.reservation_id].status, "filled")
        self.assertEqual(next(iter(engine.allocations.values())).quantity, 20)
        self.journal.close()
        self.journal = TradingJournal(Path(self.temp.name) / "portfolio.sqlite3")
        recovered = self.engine([profile])
        self.assertEqual(next(iter(recovered.allocations.values())).quantity, 20)
        self.assertEqual(recovered.reservations[decision.reservation_id].status, "filled")

    async def test_reduce_only_control_blocks_entries(self) -> None:
        profile = PortfolioAccountProfile("cash", "C1", "live", "cash", PortfolioPolicy(policy_id="control"))
        engine = self.engine([profile])
        engine.synchronize_snapshot("C1", summary=summary("C1"), ledger=ledger("C1"), positions=[])
        engine.set_control("cash", PortfolioControlMode.REDUCE_ONLY, reason="operator")
        decision, approved = await engine.approve(intent("blocked"), account_id="C1")
        self.assertEqual(decision.status, PortfolioDecisionStatus.REJECTED)
        self.assertIn("entries_paused", decision.reasons)
        self.assertIsNone(approved)

    async def test_relative_capital_request_is_resolved_inside_account_mandate(self) -> None:
        policy = PortfolioPolicy(
            policy_id="relative",
            maximum_position_fraction=1,
            maximum_ticker_fraction=1,
            maximum_planned_risk_fraction=1,
            maximum_open_risk_fraction=1,
        )
        profile = PortfolioAccountProfile(
            "primary",
            "C1",
            "replay",
            "simulated",
            policy,
            strategy_allocations={"strategy-a": 0.3},
        )
        engine = self.engine([profile])
        engine.synchronize_snapshot(
            "C1",
            summary=summary("C1", available=100_000),
            ledger=ledger("C1", cash=100_000),
            positions=[],
        )
        request = intent("relative", quantity=1)
        request = StrategyIntent(
            **{
                **request.payload(),
                "capital_request": CapitalRequest(mode="all_available"),
            }
        )

        decision, approved = await engine.approve(request, account_id="C1")

        self.assertEqual(decision.status, PortfolioDecisionStatus.RESIZED)
        self.assertIsNotNone(approved)
        self.assertEqual(approved.quantity, 300)

    async def test_capacity_rejection_can_create_explicit_rebalance_proposal(self) -> None:
        policy = PortfolioPolicy(
            policy_id="replacement",
            maximum_position_fraction=1,
            maximum_ticker_fraction=1,
            maximum_planned_risk_fraction=1,
            maximum_open_risk_fraction=1,
            maximum_open_positions=1,
        )
        profile = PortfolioAccountProfile(
            "primary",
            "C1",
            "replay",
            "simulated",
            policy,
            strategy_allocations={"strategy-a": 1.0},
            strategy_mandates={
                "strategy-a": {
                    "allow_replacement": True,
                    "minimum_replacement_improvement_pct": 20,
                    "autonomy": "confirm",
                }
            },
        )
        engine = self.engine([profile])
        engine.synchronize_snapshot(
            "C1",
            summary=summary("C1"),
            ledger=ledger("C1"),
            positions=[position("C1", "MSFT", 10)],
        )
        engine.allocations["C1:strategy-a:MSFT"] = PortfolioAllocationLot(
            allocation_id="C1:strategy-a:MSFT",
            account_key="primary",
            account_id="C1",
            strategy_id="strategy-a",
            strategy_revision=2,
            assignment_id="assignment-MSFT",
            ticker="MSFT",
            quantity=10,
            average_price=100,
            planned_risk=20,
            realized_pnl=0,
            source="test",
            updated_at=NOW,
        )
        request = intent("replace", quantity=100)
        request = StrategyIntent(
            **{
                **request.payload(),
                "capital_request": CapitalRequest(
                    mode="fixed_quantity",
                    value=100,
                    allow_replacement=True,
                ),
                "metadata": {
                    **request.metadata,
                    "opportunity_score": 0.5,
                },
            }
        )

        decision, approved = await engine.approve(request, account_id="C1")

        self.assertEqual(decision.status, PortfolioDecisionStatus.REJECTED)
        self.assertIsNone(approved)
        self.assertIn("rebalance_proposed", decision.reasons)
        self.assertEqual(engine.rebalance_proposals[-1].candidate_ticker, "MSFT")


class PortfolioConfigurationTests(unittest.TestCase):
    def test_run_plans_using_one_strategy_profile_keep_distinct_allocations(self) -> None:
        configuration = {
            "accounts": {"bindings": [{
                "account_key": "paper",
                "account_class": "paper",
                "base_currency": "USD",
                "enabled": True,
                "modes": ["paper"],
                "portfolio_policy_id": "default",
                "session_key": "ibkr-paper",
            }]},
            "portfolio": {
                "policies": [{"policy_id": "default", "revision": 1}],
                "groups": [],
                "mandates": [
                    {"account_key": "paper", "enabled": True, "run_plan_id": "plan-a", "maximum_cash_fraction": 0.2},
                    {"account_key": "paper", "enabled": True, "run_plan_id": "plan-b", "maximum_cash_fraction": 0.4},
                ],
            },
            "run_plans": {"plans": [
                {"enabled": True, "run_plan_id": "plan-a", "profile_id": "same-profile", "allowed_environments": ["paper"]},
                {"enabled": True, "run_plan_id": "plan-b", "profile_id": "same-profile", "allowed_environments": ["paper"]},
            ]},
            "strategy": {"profiles": [{"profile_id": "same-profile"}]},
        }
        profiles, _ = portfolio_profiles_from_configuration(
            [{"account_key": "paper", "account_id": "DU1", "account_class": "paper", "trading_mode": "paper"}],
            configuration,
        )
        self.assertEqual(profiles[0].strategy_allocations, {"plan-a": 0.2, "plan-b": 0.4})

    def test_durable_config_uses_stable_keys_and_cannot_broaden_registered_account(self) -> None:
        raw = """
        {
          "policies": {
            "aggressive@2": {
              "policy_id": "aggressive",
              "revision": 2,
              "allow_short": true,
              "allow_margin": true,
              "maximum_net_short_exposure": 100000
            }
          },
          "accounts": {
            "rrsp": {
              "policy": "aggressive@2",
              "session_key": "ibkr-live-primary",
              "strategy_allocations": {"strategy-a": 0.25}
            }
          }
        }
        """
        profiles, groups = configured_portfolio_profiles(
            [
                {
                    "account_key": "rrsp",
                    "account_id": "BROKER-BOUND-OUTSIDE-CONFIG",
                    "account_class": "rrsp",
                    "trading_mode": "live",
                }
            ],
            raw_config=raw,
        )
        self.assertEqual(groups, ())
        self.assertEqual(profiles[0].account_key, "rrsp")
        self.assertEqual(profiles[0].account_id, "BROKER-BOUND-OUTSIDE-CONFIG")
        self.assertFalse(profiles[0].policy.allow_short)
        self.assertFalse(profiles[0].policy.allow_margin)
        self.assertEqual(profiles[0].strategy_allocations["strategy-a"], 0.25)

    def test_runtime_loads_all_accounts_in_the_live_session_for_group_authority(self) -> None:
        accounts = """
        [
          {"key":"cash","account_id":"C1","account_class":"cash","trading_mode":"live"},
          {"key":"margin","account_id":"M1","account_class":"margin","trading_mode":"live"}
        ]
        """
        portfolio = """
        {
          "policies": {
            "cash@1": {"policy_id":"cash","revision":1},
            "margin@1": {"policy_id":"margin","revision":1,"allow_margin":true}
          },
          "accounts": {
            "cash": {"policy":"cash@1"},
            "margin": {"policy":"margin@1"}
          },
          "groups": {
            "household": {
              "accounts":["cash","margin"],
              "maximum_gross_exposure":100000,
              "maximum_ticker_exposure":25000
            }
          }
        }
        """
        with patch.dict(
            "os.environ",
            {
                "IBKR_ACCOUNTS_JSON": accounts,
                "PORTFOLIO_MANAGEMENT_JSON": portfolio,
            },
            clear=False,
        ):
            profiles, groups = configured_portfolio_profiles_for_runtime(
                ("C1",),
                mode="live",
            )
        self.assertEqual({row.account_key for row in profiles}, {"cash", "margin"})
        self.assertEqual(groups[0].account_keys, ("cash", "margin"))
