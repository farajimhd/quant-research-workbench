from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.backend.replay_run_service import (
    ReplayRunController,
    ReplayRunDefinition,
    replay_preflight,
)
from src.trading_runtime.domain import InstrumentContract
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.strategy_engine import (
    AssignmentStatus,
    AssignedLongMomentumStrategy,
    STRATEGY_ID,
    STRATEGY_REVISION,
    StrategyAssignment,
    StrategyPermissions,
    resolve_long_momentum_parameters,
)
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner


NEW_YORK = ZoneInfo("America/New_York")


class ReplayRunDefinitionTests(unittest.TestCase):
    def test_definition_builds_timezone_aware_session_boundaries(self) -> None:
        definition = ReplayRunDefinition(
            session_date=date(2026, 7, 28),
            start_time=time(9, 45),
            initial_cash=250_000,
            tickers=("AAPL",),
        )

        self.assertEqual(definition.session_start.isoformat(), "2026-07-28T04:00:00-04:00")
        self.assertEqual(definition.requested_start.isoformat(), "2026-07-28T09:45:00-04:00")
        self.assertEqual(definition.session_end.isoformat(), "2026-07-28T20:00:00-04:00")

    def test_definition_rejects_clock_outside_extended_session(self) -> None:
        with self.assertRaisesRegex(ValueError, "04:00-20:00"):
            ReplayRunDefinition(
                session_date=date(2026, 7, 28),
                start_time=time(3, 59),
            )


class ReplayControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_commands_keep_event_clock_and_transport_state_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = ReplayRunController(
                ReplayRunDefinition(
                    session_date=date(2026, 7, 28),
                    start_time=time(9, 45),
                    tickers=("AAPL",),
                    canvas_revision="canvas-test",
                    canvas_profile={"defaultState": {"openIds": ["chart"]}},
                ),
                runtime_root=Path(directory),
            )
            controller.status = "ready"
            controller.current_time = datetime(2026, 7, 28, 9, 45, tzinfo=NEW_YORK)

            played = await controller.command("play")
            self.assertEqual(played["status"], "running")
            self.assertEqual(played["current_time"], "2026-07-28T09:45:00-04:00")
            self.assertEqual(played["canvas_profile"]["defaultState"]["openIds"], ["chart"])

            stepped = await controller.command("step", step_seconds=5)
            self.assertEqual(stepped["status"], "running")
            self.assertEqual(
                controller._step_until,
                datetime(2026, 7, 28, 9, 45, 5, tzinfo=NEW_YORK),
            )

            await controller.command("set_speed", speed=120)
            self.assertEqual(controller.speed, 120)
            self.assertTrue(controller._pace_reset)

            paused = await controller.command("pause")
            self.assertEqual(paused["status"], "paused")

    async def test_step_boundary_forces_paused_state_to_subscribers(self) -> None:
        controller = ReplayRunController(
            ReplayRunDefinition(
                session_date=date(2026, 7, 28),
                start_time=time(9, 45),
                tickers=("AAPL",),
            ),
            runtime_root=Path(tempfile.gettempdir()),
        )
        boundary = datetime(2026, 7, 28, 9, 45, 1, tzinfo=NEW_YORK)
        controller.status = "running"
        controller._step_until = boundary
        queue = controller.subscribe()

        await controller._after_event(boundary)
        published = queue.get_nowait()

        self.assertEqual(controller.status, "paused")
        self.assertEqual(published["status"], "paused")
        self.assertEqual(published["current_time"], boundary.isoformat())

    async def test_fast_forward_cannot_cross_session_end(self) -> None:
        controller = ReplayRunController(
            ReplayRunDefinition(
                session_date=date(2026, 7, 28),
                start_time=time(19, 58),
                tickers=("AAPL",),
            ),
            runtime_root=Path(tempfile.gettempdir()),
        )
        controller.status = "ready"
        controller.current_time = datetime(2026, 7, 28, 19, 58, tzinfo=NEW_YORK)

        with self.assertRaisesRegex(ValueError, "cannot exceed 20:00"):
            await controller.command("fast_forward", target_time=time(20, 0, 1))


class ReplayPreflightTests(unittest.TestCase):
    def test_preflight_maps_live_accounts_to_explicit_simulated_boundaries(self) -> None:
        assignment = {
            "account_id": "DU123456",
            "assignment_id": "assignment-1",
            "conid": 265598,
            "status": "watching",
            "ticker": "AAPL",
        }
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.backend.replay_run_service.historical_gateway_snapshot",
            return_value={"base_url": "http://127.0.0.1:8801", "ready": True},
        ), patch(
            "src.backend.replay_run_service.historical_day_coverage",
            return_value={
                "coverage_table": "qmd.coverage",
                "event_count": 10_000,
                "ticker_count": 1,
            },
        ), patch(
            "src.backend.replay_run_service.list_strategy_assignments",
            return_value=[assignment],
        ), patch(
            "src.backend.replay_run_service.replay_runtime_root",
            return_value=Path(directory),
        ):
            result = replay_preflight(
                session_date=date(2026, 7, 28),
                start_time=time(9, 45),
                initial_cash=100_000,
                tickers=("AAPL",),
            )

        self.assertTrue(result["ready"])
        self.assertEqual(result["account_mapping"], {"DU123456": "SIM-01-DU123456"})
        self.assertTrue(all(check["status"] == "ready" for check in result["checks"]))


class ReplaySharedAbstractionTests(unittest.TestCase):
    def test_canvas_journal_projection_is_category_filtered_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            for index, category in enumerate(
                ("lifecycle", "strategy", "strategy_decision", "order_management"),
            ):
                journal.append(
                    run_id="replay-run",
                    category=category,
                    entity_type="test",
                    entity_id=str(index),
                    payload={"index": index},
                )

            records = journal.recent_records(
                "replay-run",
                categories=("strategy", "strategy_decision", "order_management"),
                limit=2,
            )
            journal.close()

        self.assertEqual([record.category for record in records], ["strategy_decision", "order_management"])

    def test_runtime_planner_accepts_identity_for_in_run_assignment(self) -> None:
        planner = RuntimeIbkrStrategyOrderPlanner(
            {},
            strategy_id=STRATEGY_ID,
            strategy_revision=STRATEGY_REVISION,
        )
        planner.upsert_instrument(
            InstrumentContract(
                instrument_id="simulated:265598",
                conid=265598,
                symbol="AAPL",
                security_type="STK",
                currency="USD",
                exchange="SMART",
            )
        )

        self.assertEqual(planner.instruments["AAPL"].conid, 265598)

    def test_assignment_commands_update_shared_strategy_state(self) -> None:
        observed_at = datetime(2026, 7, 28, 9, 45, tzinfo=NEW_YORK)
        assignment = StrategyAssignment(
            assignment_id="assignment-1",
            strategy_id=STRATEGY_ID,
            strategy_revision=STRATEGY_REVISION,
            account_id="SIM-REPLAY",
            ticker="AAPL",
            conid=265598,
            status=AssignmentStatus.PAUSED,
            permissions=StrategyPermissions(),
            parameters=resolve_long_momentum_parameters({}),
            state={},
            source="test",
            created_at=observed_at,
            updated_at=observed_at,
        )
        strategy = AssignedLongMomentumStrategy([assignment])

        updated = strategy.command_assignment(
            assignment.assignment_id,
            "force_entry",
            event_time=observed_at,
        )

        self.assertEqual(updated.status, AssignmentStatus.PAUSED)
        self.assertTrue(updated.state["force_entry_requested"])


if __name__ == "__main__":
    unittest.main()
