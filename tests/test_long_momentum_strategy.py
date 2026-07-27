from __future__ import annotations

import tempfile
import unittest
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.strategy_engine import (
    AssignedLongMomentumStrategy,
    AssignmentStatus,
    LongMomentumStrategyEngine,
    STRATEGY_ID,
    STRATEGY_REVISION,
    StrategyAssignment,
    StrategyObservation,
    StrategyPermissions,
    default_long_momentum_parameters,
    long_momentum_strategy_definition,
)
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner, RuntimeIbkrStrategyOrderPlanner
from src.trading_runtime.runtime import RunConfig, RunMode, TradingRuntime
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
from src.backend import trading_runtime_service


NOW = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)


def assignment(
    *,
    status: AssignmentStatus = AssignmentStatus.WATCHING,
    permissions: StrategyPermissions | None = None,
    state: dict | None = None,
) -> StrategyAssignment:
    return StrategyAssignment(
        assignment_id="assignment-1",
        strategy_id=STRATEGY_ID,
        strategy_revision=STRATEGY_REVISION,
        account_id="DU123",
        ticker="AAPL",
        conid=265598,
        status=status,
        permissions=permissions or StrategyPermissions(enter=True, add=True, reenter=True),
        parameters=default_long_momentum_parameters(),
        state=state or {},
        created_at=NOW,
        updated_at=NOW,
    )


def confirmed_observation(**overrides) -> StrategyObservation:
    payload = {
        "ticker": "AAPL",
        "observed_at": NOW,
        "price": 101.0,
        "bid": 100.99,
        "ask": 101.01,
        "previous_close": 100.0,
        "previous_high": 100.5,
        "swing_high": 100.5,
        "swing_low": 99.5,
        "vwap": 100.2,
        "vwap_slope_bps_per_second": 1.0,
        "macd_line": 0.4,
        "macd_signal": 0.2,
        "macd_histogram": 0.2,
        "qmd_score": 0.6,
        "qmd_confidence": 0.8,
        "qmd_bias": "bullish",
        "volatility": 0.4,
        "upper_luld_price": 110.0,
    }
    payload.update(overrides)
    return StrategyObservation(**payload)


class LongMomentumStrategyTests(unittest.TestCase):
    def test_definition_is_long_only_timeframe_aware_and_searchable(self) -> None:
        definition = long_momentum_strategy_definition()
        config = definition["config"]
        self.assertEqual(config["direction"], "long_only")
        self.assertIn("100ms", config["parameter_space"]["entry.breakout_timeframe"])
        self.assertEqual(config["taxonomy"]["indicators"][0]["timeframe"], "100ms")
        self.assertIn("position_event", config["taxonomy"]["evaluation_triggers"])

    def test_confirmed_swing_break_enters_with_semantic_protection(self) -> None:
        result = LongMomentumStrategyEngine().evaluate(assignment(), confirmed_observation())
        self.assertEqual(result.status, AssignmentStatus.ENTRY_PENDING)
        self.assertEqual(result.evaluation.signals[0].action, "enter_long")
        intent = result.evaluation.intents[0]
        self.assertLess(intent.invalidation_price or 0, intent.reference_price)
        self.assertGreater(intent.profit_target_price or 0, intent.reference_price)
        self.assertGreater(intent.trailing_amount or 0, 0)

    def test_veto_blocks_entry_even_when_breakout_is_confirmed(self) -> None:
        result = LongMomentumStrategyEngine().evaluate(
            assignment(), confirmed_observation(liquidity_dislocation_score=0.9)
        )
        self.assertEqual(result.evaluation.signals[0].action, "wait")
        self.assertEqual(result.evaluation.signals[0].reason, "entry_vetoed")
        self.assertFalse(result.evaluation.intents)

    def test_manual_position_is_managed_without_entry_permission(self) -> None:
        managed = assignment(
            status=AssignmentStatus.MANAGING,
            permissions=StrategyPermissions(enter=False, add=False, reduce=True, exit=True),
            state={
                "active_stop": 99.0,
                "initial_stop": 99.0,
                "breakout_level": 100.5,
                "entry_reference_price": 101.0,
                "high_water_price": 101.0,
            },
        )
        result = LongMomentumStrategyEngine().evaluate(
            managed,
            confirmed_observation(price=100.0, position_quantity=100, average_price=101.0),
        )
        self.assertEqual(result.evaluation.signals[0].action, "exit")
        self.assertEqual(result.evaluation.signals[0].reason, "failed_breakout")

    def test_bullish_choch_adds_only_with_confirmation(self) -> None:
        managed = assignment(
            status=AssignmentStatus.MANAGING,
            state={
                "active_stop": 99.0,
                "initial_stop": 99.0,
                "breakout_level": 100.5,
                "entry_reference_price": 101.0,
                "high_water_price": 101.2,
                "adds": 0,
            },
        )
        result = LongMomentumStrategyEngine().evaluate(
            managed,
            confirmed_observation(
                price=101.3,
                position_quantity=100,
                average_price=101.0,
                structure_event="choch",
                structure_direction="bullish",
            ),
        )
        self.assertEqual(result.evaluation.signals[0].action, "add_long")
        self.assertEqual(result.evaluation.intents[0].quantity, 50)

    def test_profit_pocket_closes_the_campaign_episode_before_reentry(self) -> None:
        managed = assignment(
            status=AssignmentStatus.MANAGING,
            state={
                "active_stop": 99.0,
                "initial_stop": 99.0,
                "breakout_level": 100.5,
                "entry_reference_price": 101.0,
                "high_water_price": 102.0,
                "last_acceleration": 0.3,
            },
        )
        result = LongMomentumStrategyEngine().evaluate(
            managed,
            confirmed_observation(
                price=102.0,
                position_quantity=100,
                average_price=101.0,
                acceleration=0.1,
            ),
        )
        self.assertEqual(result.evaluation.signals[0].action, "exit")
        self.assertEqual(result.evaluation.signals[0].reason, "profit_pocket")
        self.assertEqual(result.evaluation.intents[0].quantity, 100)
        self.assertEqual(result.status, AssignmentStatus.REENTRY_COOLDOWN)

    def test_reentry_cooldown_is_deterministic(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["reentry"]["cooldown_ms"] = 5000
        waiting = assignment(
            status=AssignmentStatus.REENTRY_COOLDOWN,
            state={"reentries": 1, "last_exit_at": NOW.isoformat()},
        )
        waiting = StrategyAssignment(**{**waiting.payload(), "status": AssignmentStatus.REENTRY_COOLDOWN, "permissions": waiting.permissions, "parameters": parameters, "created_at": NOW, "updated_at": NOW})
        result = LongMomentumStrategyEngine().evaluate(
            waiting, confirmed_observation(observed_at=NOW + timedelta(seconds=1))
        )
        self.assertEqual(result.evaluation.signals[0].reason, "reentry_cooldown")

    def test_ibkr_entry_plan_has_parent_target_hard_stop_and_trailing_stop(self) -> None:
        result = LongMomentumStrategyEngine().evaluate(assignment(), confirmed_observation())
        plan = IbkrStrategyOrderPlanner().plan(
            account_id="DU123",
            instrument=InstrumentContract("ibkr:265598", 265598, "AAPL", "STK", "USD"),
            intent=result.evaluation.intents[0],
            strategy_id=STRATEGY_ID,
            strategy_revision=STRATEGY_REVISION,
        )
        self.assertEqual([order.orderType for order in plan.orders], ["LMT", "LMT", "STP", "TRAIL"])
        self.assertTrue(all(order.parentId == plan.orders[0].cOID for order in plan.orders[1:]))
        self.assertTrue(all(order.isSingleGroup for order in plan.orders))
        self.assertTrue(all(not order.cOID for order in plan.orders[1:]))
        self.assertTrue(all("strategy" not in order.to_cpapi() for order in plan.orders))
        self.assertTrue(all("strategy_intent_id" not in order.to_cpapi() for order in plan.orders))

    def test_full_exit_replaces_existing_protection_with_one_oca_group(self) -> None:
        managed = assignment(
            status=AssignmentStatus.MANAGING,
            state={
                "active_stop": 99.0,
                "initial_stop": 99.0,
                "breakout_level": 100.5,
                "entry_reference_price": 101.0,
                "high_water_price": 101.0,
            },
        )
        result = LongMomentumStrategyEngine().evaluate(
            managed,
            confirmed_observation(price=100.0, position_quantity=100, average_price=101.0),
        )
        plan = IbkrStrategyOrderPlanner().plan(
            account_id="DU123",
            instrument=InstrumentContract("ibkr:265598", 265598, "AAPL", "STK", "USD"),
            intent=result.evaluation.intents[0],
            strategy_id=STRATEGY_ID,
            strategy_revision=STRATEGY_REVISION,
        )
        self.assertEqual([order.orderType for order in plan.orders], ["LMT", "STP", "TRAIL"])
        self.assertTrue(plan.cancel_strategy_protection)
        self.assertTrue(all(order.isSingleGroup for order in plan.orders))
        self.assertTrue(all(order.parentId is None for order in plan.orders))
        self.assertTrue(all(order.cOID for order in plan.orders))

    def test_assignments_and_decisions_are_durable_and_point_in_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            payload = assignment().payload()
            journal.save_strategy_assignment(payload)
            journal.append(
                run_id="assignment-1",
                category="strategy",
                entity_type="strategy_evaluation",
                entity_id="decision-1",
                event_time=NOW,
                payload={
                    "strategy_id": STRATEGY_ID,
                    "strategy_revision": STRATEGY_REVISION,
                    "ticker": "AAPL",
                    "action": "enter_long",
                },
            )
            self.assertEqual(journal.strategy_assignment("assignment-1")["ticker"], "AAPL")
            self.assertFalse(
                journal.strategy_records(
                    ticker="AAPL", strategy_id=STRATEGY_ID, as_of=NOW - timedelta(milliseconds=1)
                )
            )
            self.assertEqual(
                len(journal.strategy_records(ticker="AAPL", strategy_id=STRATEGY_ID, as_of=NOW)),
                1,
            )
            journal.close()


class LongMomentumRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_runtime_records_intent_and_submits_protected_order_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            assigned = assignment()
            strategy = AssignedLongMomentumStrategy([assigned])
            instrument = InstrumentContract("ibkr:265598", 265598, "AAPL", "STK", "USD")
            runtime = TradingRuntime(
                RunConfig(
                    mode=RunMode.PAPER,
                    strategy_id=STRATEGY_ID,
                    strategy_revision=STRATEGY_REVISION,
                    account_ids=("DU123",),
                    anchor_date=date(2026, 7, 24),
                    run_id="runtime-1",
                ),
                SimulatedBrokerAdapter(["DU123"], mode=TradingMode.PAPER),
                strategy,
                journal,
                intent_planner=RuntimeIbkrStrategyOrderPlanner(
                    {"AAPL": instrument},
                    strategy_id=STRATEGY_ID,
                    strategy_revision=STRATEGY_REVISION,
                ),
            )
            await runtime.initialize()
            await runtime.process_strategy_observation(confirmed_observation())
            self.assertEqual(len(await runtime.broker.live_orders()), 4)
            records = journal.records("runtime-1")
            self.assertTrue(any(row.entity_type == "strategy_intent" for row in records))
            self.assertTrue(any(row.entity_type == "strategy_assignment_state" for row in records))
            self.assertEqual(journal.strategy_assignment("assignment-1")["status"], "entry_pending")
            journal.close()


class LongMomentumServiceTests(unittest.TestCase):
    def test_assignment_api_service_persists_definition_evaluation_and_canvas_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("TRADING_JOURNAL_PATH")
            os.environ["TRADING_JOURNAL_PATH"] = str(Path(directory) / "service.sqlite3")
            trading_runtime_service.trading_journal.cache_clear()
            try:
                rows = trading_runtime_service.list_strategy_definitions()
                self.assertEqual([row["strategy_id"] for row in rows], [STRATEGY_ID])
                created = trading_runtime_service.create_strategy_assignment(
                    {
                        "account_id": "DU123",
                        "ticker": "AAPL",
                        "conid": 265598,
                        "permissions": {"enter": True, "add": True, "reenter": True},
                    }
                )
                observation = confirmed_observation().payload()
                evaluated = trading_runtime_service.evaluate_strategy_assignment(
                    created["assignment_id"], observation
                )
                self.assertEqual(evaluated["evaluation"]["action"], "enter_long")
                self.assertEqual(len(evaluated["order_plan"]), 4)
                self.assertFalse(evaluated["orders_submitted"])
                canvas = trading_runtime_service.strategy_canvas_payload(as_of=NOW, ticker="AAPL")
                self.assertEqual(canvas["historical_source"], "saved_strategy_journal_only")
                self.assertEqual(len(canvas["signals"]), 1)
            finally:
                journal = trading_runtime_service.trading_journal()
                journal.close()
                trading_runtime_service.trading_journal.cache_clear()
                if previous is None:
                    os.environ.pop("TRADING_JOURNAL_PATH", None)
                else:
                    os.environ["TRADING_JOURNAL_PATH"] = previous


if __name__ == "__main__":
    unittest.main()
