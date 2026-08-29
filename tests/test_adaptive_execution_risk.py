from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.execution_policies import (
    AddProtectionPolicy,
    ExecutionEnvelope,
    ExecutionMarketDataProvider,
    ExecutionMarketSnapshot,
    ExecutionPolicy,
    ExecutionPolicyName,
    PartialFillPolicy,
    ProtectionProfile,
    ProtectionSlice,
    StopRule,
    StopRuleType,
    StructuralAnchor,
)
from src.trading_runtime.ibkr_schema import AccountLedger, AccountSummary, LiveOrder, OrderRequest, OrderStatus
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.order_management import BrokerCommunicationPolicy, OrderManagementEngine
from src.trading_runtime.portfolio import (
    PortfolioAccountProfile,
    PortfolioManagementEngine,
    PortfolioPolicy,
)
from src.trading_runtime.risk import RiskAuthority
from src.trading_runtime.risk_supervisor import AccountRiskState, ContinuousRiskSupervisor
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner, StrategyOrderPlan


NOW = datetime.now(timezone.utc)
RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\quant-research-workbench\test-temp")


class RecordingBroker(SimulatedBrokerAdapter):
    def __init__(self) -> None:
        super().__init__(["DU1"], mode=TradingMode.PAPER)
        self.modifications: list[tuple[str, OrderRequest]] = []

    async def modify_order(self, account_id: str, order_id: str, order: OrderRequest):
        self.modifications.append((order_id, order))
        return await super().modify_order(account_id, order_id, order)


def two_slice_profile() -> ProtectionProfile:
    return ProtectionProfile(
        profile_id="swing-ladder",
        revision=2,
        slices=(
            ProtectionSlice("near-swing", 0.5, StopRule(StopRuleType.FIXED_PRICE, price=95)),
            ProtectionSlice("deep-swing", 0.5, StopRule(StopRuleType.FIXED_PRICE, price=90)),
        ),
    )


def adaptive_intent(*, quantity: float = 100) -> StrategyIntent:
    return StrategyIntent(
        intent_id="adaptive-entry",
        ticker="TEST",
        event_time=NOW,
        action="enter_long",
        quantity=quantity,
        reference_price=100,
        execution_policy=ExecutionPolicy(
            policy_id="qmd-regular",
            revision=3,
            name=ExecutionPolicyName.ADAPTIVE_REGULAR,
            envelope=ExecutionEnvelope(
                maximum_buy_price=110,
                deadline_ms=1_000,
                maximum_reprices=4,
                minimum_reprice_interval_ms=0,
            ),
            partial_fill_policy=PartialFillPolicy.COMPLETE_REMAINDER,
        ),
        protection_profile=two_slice_profile(),
        metadata={"assignment_id": "assignment-test"},
    )


def portfolio_approved(
    journal: TradingJournal,
    request: StrategyIntent,
    account_id: str = "DU1",
) -> StrategyIntent:
    decision_id = f"decision:{request.intent_id}"
    reservation_id = f"reservation:{request.intent_id}"
    state = journal.portfolio_states().get(account_id) or {}
    reservations = [
        row
        for row in state.get("reservations") or []
        if str(row.get("reservation_id") or "") != reservation_id
    ]
    reservations.append(
        {
            "reservation_id": reservation_id,
            "decision_id": decision_id,
            "intent_id": request.intent_id,
            "account_id": account_id,
            "ticker": request.ticker,
            "quantity": request.quantity,
            "status": "reserved",
        }
    )
    journal.save_portfolio_state(account_id, {**state, "reservations": reservations})
    return replace(
        request,
        metadata={
            **request.metadata,
            "portfolio_decision_id": decision_id,
            "portfolio_reservation_id": reservation_id,
        },
    )


class ContractAndPlanningTests(unittest.TestCase):
    def test_hybrid_stop_ignores_structural_anchor_on_wrong_side_of_entry(self) -> None:
        rule = StopRule(
            StopRuleType.HYBRID,
            volatility_multiple=1.25,
            buffer_bps=8,
            anchor=StructuralAnchor(
                observation_id="qmd-derived:SUGP:1s:231",
                price=3.33,
                confirmed_at=NOW,
                timeframe="strategy",
            ),
        )

        resolved = rule.resolve(
            reference_price=3.3195,
            side="long",
            quantity=3_000,
            volatility=0.013689117957856283,
        )

        self.assertAlmostEqual(resolved, 3.3023886025526797)

    def test_multi_swing_profile_creates_independent_protected_batches(self) -> None:
        plan = IbkrStrategyOrderPlanner().plan(
            account_id="DU1",
            instrument=InstrumentContract("TEST", 123, "TEST", "STK", "USD"),
            intent=adaptive_intent(quantity=101),
            strategy_id="strategy",
            strategy_revision=7,
        )

        self.assertEqual(len(plan.broker_batches), 2)
        self.assertEqual([batch[0].quantity for batch in plan.broker_batches], [51, 50])
        self.assertTrue(all(batch[1].parentId == batch[0].cOID for batch in plan.broker_batches))
        self.assertEqual([batch[1].auxPrice for batch in plan.broker_batches], [95, 90])
        self.assertEqual(plan.order_slice_ids, ("near-swing", "near-swing", "deep-swing", "deep-swing"))

    def test_add_can_inherit_the_existing_position_stop(self) -> None:
        base = adaptive_intent(quantity=10)
        inherited_profile = ProtectionProfile(
            profile_id="inherit-stop",
            revision=1,
            slices=base.protection_profile.slices if base.protection_profile else (),
            add_policy=AddProtectionPolicy.INHERIT_POSITION_STOP,
        )
        add = replace(
            base,
            intent_id="adaptive-add",
            action="add_long",
            protection_profile=inherited_profile,
            metadata={**base.metadata, "position_stop_price": 97},
        )
        plan = IbkrStrategyOrderPlanner().plan(
            account_id="DU1",
            instrument=InstrumentContract("TEST", 123, "TEST", "STK", "USD"),
            intent=add,
            strategy_id="strategy",
            strategy_revision=7,
        )
        self.assertEqual([batch[1].auxPrice for batch in plan.broker_batches], [97, 97])


class OrderSafetyExitTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_exit_alternatives_do_not_multiply_approved_quantity(self) -> None:
        broker = SimulatedBrokerAdapter(["DU1"], mode=TradingMode.PAPER)
        await broker.initialize()
        risk = RiskAuthority()
        await risk.prime(broker, ["DU1"])
        request = replace(
            adaptive_intent(quantity=100),
            intent_id="full-exit",
            action="exit",
            invalidation_price=95,
            protection_profile=None,
        )
        plan = IbkrStrategyOrderPlanner().plan(
            account_id="DU1",
            instrument=InstrumentContract("TEST", 123, "TEST", "STK", "USD"),
            intent=request,
            strategy_id="strategy",
            strategy_revision=7,
        )

        self.assertEqual(len(plan.orders), 2)
        await risk.validate(
            broker,
            "DU1",
            list(plan.orders),
            intent=request,
            require_fresh=False,
        )


class PortfolioAndContinuousRiskTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=RUNTIME_ROOT)
        self.journal = TradingJournal(Path(self.temp.name) / "risk.sqlite3")

    def tearDown(self) -> None:
        self.journal.close()
        self.temp.cleanup()

    def engine(self, policy: PortfolioPolicy) -> PortfolioManagementEngine:
        engine = PortfolioManagementEngine(
            [PortfolioAccountProfile("paper-main", "DU1", "paper", "margin", policy)],
            journal=self.journal,
            run_id="risk-test",
            strategy_id="strategy",
            strategy_revision=7,
        )
        engine.synchronize_snapshot(
            "DU1",
            summary=AccountSummary("DU1", 100_000, 100_000, 100_000, 0, 100_000, 100_000, timestamp=NOW),
            ledger=AccountLedger("DU1", 100_000, 100_000, 0, 100_000, 0, 0, timestamp=NOW),
            positions=[],
        )
        return engine

    async def test_portfolio_sizes_from_worst_entry_envelope_and_each_stop_slice(self) -> None:
        engine = self.engine(
            PortfolioPolicy(
                policy_id="bounded",
                maximum_position_fraction=1,
                maximum_ticker_fraction=1,
                maximum_planned_risk_fraction=0.01,
                maximum_open_risk_fraction=1,
                maximum_order_notional=1_000_000,
            )
        )
        decision, approved = await engine.approve(adaptive_intent(quantity=1_000), account_id="DU1")

        self.assertIsNotNone(approved)
        self.assertAlmostEqual(decision.approved_quantity, 57.142857, places=6)
        reservation = next(iter(engine.reservations.values()))
        self.assertAlmostEqual(reservation.reference_price, 110)
        self.assertLessEqual(reservation.reserved_planned_risk, 1_000.00001)

    async def test_continuous_risk_latches_and_requires_explicit_resume(self) -> None:
        engine = self.engine(
            PortfolioPolicy(
                policy_id="loss-bands",
                maximum_daily_loss=1_000,
                maximum_drawdown=2_000,
                daily_loss_warning=500,
                emergency_loss=1_500,
            )
        )
        supervisor = ContinuousRiskSupervisor(engine, journal=self.journal, run_id="risk-test")

        warning = await supervisor.evaluate("DU1", reason="account_update")
        self.assertEqual(warning.state, AccountRiskState.NORMAL)
        engine.states["DU1"].realized_pnl_today = -600
        warning = await supervisor.evaluate("DU1", reason="account_update")
        self.assertEqual(warning.state, AccountRiskState.ENTRIES_PAUSED)
        engine.states["DU1"].realized_pnl_today = 0
        latched = await supervisor.evaluate("DU1", reason="account_update")
        self.assertEqual(latched.state, AccountRiskState.ENTRIES_PAUSED)
        resumed = await supervisor.resume("DU1", reason="operator_reviewed")
        self.assertEqual(resumed.state, AccountRiskState.NORMAL)

    async def test_broker_refresh_preserves_externally_queued_operator_command(self) -> None:
        engine = self.engine(PortfolioPolicy(policy_id="operator-state"))
        engine.apply_persisted_operational_state(
            "DU1",
            {
                "account_key": "paper-main",
                "control_mode": "reduce_only",
                "pending_operational_commands": [
                    {
                        "command_id": "kill-1",
                        "command": "kill_entries",
                        "status": "pending",
                    }
                ],
            },
        )
        engine.synchronize_snapshot(
            "DU1",
            summary=AccountSummary("DU1", 100_000, 100_000, 100_000, 0, 100_000, 100_000, timestamp=NOW),
            ledger=AccountLedger("DU1", 100_000, 100_000, 0, 100_000, 0, 0, timestamp=NOW),
            positions=[],
        )
        persisted = self.journal.portfolio_states()["DU1"]
        self.assertEqual(persisted["control_mode"], "reduce_only")
        self.assertEqual(
            persisted["pending_operational_commands"][0]["command_id"],
            "kill-1",
        )


class AdaptiveOmsTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_fill_reprices_only_the_live_remainder_from_new_qmd_quote(self) -> None:
        with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as directory:
            broker = RecordingBroker()
            await broker.initialize()
            journal = TradingJournal(Path(directory) / "adaptive.sqlite3")
            risk = RiskAuthority()
            await risk.prime(broker, ["DU1"])
            market_data = ExecutionMarketDataProvider()
            market_data.update(ExecutionMarketSnapshot("TEST", 99.98, 100.02, 0.01, NOW, "qmd"))

            def planner(intent: StrategyIntent, account_id: str, _event) -> StrategyOrderPlan:
                return StrategyOrderPlan(
                    orders=(
                        OrderRequest(
                            acctId=account_id,
                            conid=123,
                            cOID=intent.intent_id,
                            ticker=intent.ticker,
                            orderType="LMT",
                            side="BUY",
                            quantity=intent.quantity,
                            price=intent.reference_price,
                        ),
                    )
                )

            manager = OrderManagementEngine(
                broker=broker,
                planner=planner,
                risk=risk,
                journal=journal,
                run_id="adaptive-test",
                strategy_id="strategy",
                strategy_revision=7,
                policy=BrokerCommunicationPolicy(),
                execution_market_data=market_data,
            )
            submitted = await manager.submit_intent(
                portfolio_approved(journal, adaptive_intent()),
                account_id="DU1",
                event=None,
            )
            broker_id = submitted.broker_order_ids[0]
            await manager.on_order_update(
                LiveOrder(
                    account="DU1",
                    orderId=broker_id,
                    conid=123,
                    ticker="TEST",
                    side="BUY",
                    orderType="LMT",
                    tif="DAY",
                    totalSize=100,
                    filledQuantity=40,
                    remainingQuantity=60,
                    avgPrice=100,
                    order_status=OrderStatus.SUBMITTED,
                    cOID="adaptive-entry",
                    price=100,
                )
            )
            manager.on_market_snapshot(
                ExecutionMarketSnapshot(
                    "TEST",
                    100.04,
                    100.06,
                    0.01,
                    datetime.now(timezone.utc),
                    "qmd",
                )
            )
            await asyncio.sleep(0.08)

            self.assertTrue(broker.modifications)
            self.assertEqual(broker.modifications[-1][0], broker_id)
            self.assertEqual(broker.modifications[-1][1].quantity, 100)
            self.assertLessEqual(float(broker.modifications[-1][1].price or 0), 110)
            self.assertEqual(manager.snapshots()[0].remaining_quantity, 60)
            await manager.close()
            journal.close()

    async def test_nonterminal_oms_state_recovers_across_runtime_run_ids(self) -> None:
        with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as directory:
            broker = RecordingBroker()
            await broker.initialize()
            journal = TradingJournal(Path(directory) / "recovery.sqlite3")

            def planner(intent: StrategyIntent, account_id: str, _event) -> StrategyOrderPlan:
                return StrategyOrderPlan(
                    (
                        OrderRequest(
                            acctId=account_id,
                            conid=123,
                            cOID=intent.intent_id,
                            ticker=intent.ticker,
                            orderType="LMT",
                            side="BUY",
                            quantity=intent.quantity,
                            price=intent.reference_price,
                        ),
                    )
                )

            first_risk = RiskAuthority()
            await first_risk.prime(broker, ["DU1"])
            first = OrderManagementEngine(
                broker=broker,
                planner=planner,
                risk=first_risk,
                journal=journal,
                run_id="before-restart",
                strategy_id="strategy",
                strategy_revision=7,
            )
            original = await first.submit_intent(
                portfolio_approved(journal, adaptive_intent(quantity=10)),
                account_id="DU1",
                event=None,
            )
            await first.close()

            second_risk = RiskAuthority()
            await second_risk.prime(broker, ["DU1"])
            second = OrderManagementEngine(
                broker=broker,
                planner=planner,
                risk=second_risk,
                journal=journal,
                run_id="after-restart",
                strategy_id="strategy",
                strategy_revision=7,
            )
            recovered = await second.recover()
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0].group_id, original.group_id)
            self.assertEqual(recovered[0].broker_order_ids, original.broker_order_ids)
            with self.assertRaisesRegex(ValueError, "already been submitted"):
                await second.submit_intent(
                    portfolio_approved(journal, adaptive_intent(quantity=10)),
                    account_id="DU1",
                    event=None,
                )
            await second.close()
            journal.close()


if __name__ == "__main__":
    unittest.main()
