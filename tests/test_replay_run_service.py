from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time as wall_time
import unittest
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, date, datetime, time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from src.backend.replay_run_service import (
    HistoricalDebugFixture,
    ReplayDerivedFrame,
    ReplayFrameSpool,
    ReplayRunController,
    ReplayRunCapacityError,
    ReplayRunDefinition,
    ReplayRunService,
    ReplaySignalEvent,
    _append_historical_derived_message,
    _attach_historical_signals,
    _canvas_profile_tickers,
    _historical_watchlist_membership_timeline_for_configuration,
    _historical_watchlist_membership_timeline_from_plans,
    _historical_watchlist_plans_at_source_native_events,
    _historical_signal_events,
    _historical_derived_frames,
    _occurrence_source_values,
    _qmd_payload_authority,
    _retryable_historical_stream_error,
    _simulation_config,
    _strategy_evaluation_end,
    _debug_derived_frames,
    _debug_market_events,
    _debug_watchlist_membership_timeline,
    backtest_debug_preflight,
    backtest_preflight,
    replay_preflight,
    replay_history_fetch_concurrency,
)
from src.market_engine.historical_source import QmdHistoricalEventSource
from src.trading_runtime.domain import InstrumentContract
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.portfolio import PortfolioPolicy
from src.trading_runtime.strategy_engine import (
    AssignmentStatus,
    AssignedLongMomentumStrategy,
    STRATEGY_ID,
    STRATEGY_REVISION,
    StrategyAssignment,
    StrategyPermissions,
    resolve_long_momentum_parameters,
    default_long_momentum_parameters,
)
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.runtime import RunMode


NEW_YORK = ZoneInfo("America/New_York")


def approved_configuration(*, assignments: list[dict] | None = None) -> dict:
    return {
        "revision_id": "configuration-test",
        "revision": 1,
        "label": "Test configuration",
        "content_hash": "test-hash",
        "approved_at": "2026-07-28T12:00:00+00:00",
        "payload": {
            "schema_version": 1,
            "strategy": {
                "strategy_id": STRATEGY_ID,
                "revision": STRATEGY_REVISION,
                "name": "Long Momentum Campaign",
                "parameters": default_long_momentum_parameters(),
            },
            "assignments": assignments or [],
            "portfolio": {"policies": [asdict(PortfolioPolicy())], "groups": []},
            "oms": {
                "entry_urgency": "urgent",
                "exit_urgency": "very_urgent",
                "limit_offset_bps": 5.0,
                "tick_size": 0.01,
                "time_in_force": "DAY",
                "outside_rth": False,
                "protection": {
                    "stop_method": "hybrid",
                    "structure_buffer_bps": 8.0,
                    "volatility_multiple": 1.25,
                    "maximum_risk_pct": 1.5,
                    "trailing_enabled": True,
                },
            },
            "accounts": {
                "bindings": [{
                    "account_key": "primary",
                    "source_account_id": "DU123456",
                    "account_class": "simulated",
                    "base_currency": "USD",
                    "session_key": "replay",
                    "portfolio_policy_id": "default",
                    "enabled": True,
                    "modes": ["replay", "backtest", "backtest_debug"],
                }]
            },
            "canvas": {
                "revision": "canvas-test",
                "profile": {
                    "defaultState": {"openIds": ["chart"]},
                    "linkContexts": {"chart": {"symbol": "AAPL"}},
                },
            },
        },
    }


class ReplayRunDefinitionTests(unittest.TestCase):
    def test_backtest_simulation_profiles_pin_baseline_and_stress_costs(self) -> None:
        baseline = ReplayRunDefinition(
            session_date=date(2026, 7, 28),
            start_time=time(4, 0),
            configuration_revision=approved_configuration(),
            mode=RunMode.BACKTEST,
            simulation_profile="baseline",
        )
        stress = ReplayRunDefinition(
            session_date=date(2026, 7, 28),
            start_time=time(4, 0),
            configuration_revision=approved_configuration(),
            mode=RunMode.BACKTEST,
            simulation_profile="stress",
        )

        baseline_config = _simulation_config(baseline)
        stress_config = _simulation_config(stress)
        self.assertEqual(baseline_config.commission_per_share, 0.005)
        self.assertEqual(baseline_config.minimum_commission, 1.0)
        self.assertEqual(baseline_config.liquidity_participation, 0.25)
        self.assertEqual(baseline_config.market_slippage_bps, 5.0)
        self.assertEqual(stress_config.liquidity_participation, 0.10)
        self.assertEqual(stress_config.market_slippage_bps, 10.0)

    def test_definition_builds_timezone_aware_session_boundaries(self) -> None:
        definition = ReplayRunDefinition(
            session_date=date(2026, 7, 28),
            start_time=time(9, 45),
            initial_cash=250_000,
            tickers=("AAPL",),
            configuration_revision=approved_configuration(),
        )

        self.assertEqual(definition.session_start.isoformat(), "2026-07-28T04:00:00-04:00")
        self.assertEqual(definition.requested_start.isoformat(), "2026-07-28T09:45:00-04:00")
        self.assertEqual(definition.session_end.isoformat(), "2026-07-28T20:00:00-04:00")

    def test_definition_pins_bounded_premarket_end_clock(self) -> None:
        definition = ReplayRunDefinition(
            session_date=date(2026, 8, 21),
            start_time=time(4, 0),
            end_time=time(9, 30),
            configuration_revision=approved_configuration(),
            mode=RunMode.BACKTEST,
        )

        self.assertEqual(definition.session_end.isoformat(), "2026-08-21T09:30:00-04:00")
        self.assertEqual(definition.payload()["end_time"], "09:30:00")

    def test_definition_rejects_end_before_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot precede"):
            ReplayRunDefinition(
                session_date=date(2026, 8, 21),
                start_time=time(7, 30),
                end_time=time(7, 29),
                configuration_revision=approved_configuration(),
                mode=RunMode.BACKTEST,
            )

    def test_definition_rejects_clock_outside_extended_session(self) -> None:
        with self.assertRaisesRegex(ValueError, "04:00-20:00"):
            ReplayRunDefinition(
                session_date=date(2026, 7, 28),
                start_time=time(3, 59),
                configuration_revision=approved_configuration(),
            )

    def test_backtest_snapshot_exposes_mode_and_pinned_canvas(self) -> None:
        definition = ReplayRunDefinition(
            session_date=date(2026, 7, 28),
            start_time=time(9, 45),
            mode=RunMode.BACKTEST,
            configuration_revision=approved_configuration(),
        )
        with tempfile.TemporaryDirectory() as directory:
            snapshot = ReplayRunController(definition, runtime_root=Path(directory)).snapshot()

        self.assertEqual(snapshot["mode"], "backtest")
        self.assertEqual(snapshot["canvas_revision"], "canvas-test")
        self.assertEqual(snapshot["canvas_profile"]["defaultState"]["openIds"], ["chart"])
        self.assertEqual(snapshot["checkpoint"]["status"], "pending")
        self.assertFalse(snapshot["checkpoint"]["resume_supported"])
        self.assertEqual(snapshot["lifecycle"]["state"], "created")
        self.assertFalse(
            next(
                row
                for row in snapshot["lifecycle"]["commands"]
                if row["command"] == "resume"
            )["enabled"]
        )

    def test_debug_definition_requires_a_bounded_deterministic_fixture(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires a deterministic fixture"):
            ReplayRunDefinition(
                session_date=date(2026, 7, 28),
                start_time=time(9, 45),
                mode=RunMode.BACKTEST_DEBUG,
                configuration_revision=approved_configuration(),
            )
        fixture = HistoricalDebugFixture(
            fixture_id="opening-range-case-1",
            market_events=({
                "kind": "trade",
                "ticker": "AAPL",
                "ts": "2026-07-28T09:45:00-04:00",
                "price": 101.25,
                "size": 100,
            },),
        )
        definition = ReplayRunDefinition(
            session_date=date(2026, 7, 28),
            start_time=time(9, 45),
            mode=RunMode.BACKTEST_DEBUG,
            debug_fixture=fixture,
            configuration_revision=approved_configuration(),
        )

        self.assertEqual(definition.payload()["mode"], "backtest_debug")
        self.assertEqual(definition.payload()["debug_fixture"]["market_event_count"], 1)
        self.assertEqual(len(definition.payload()["debug_fixture"]["content_hash"]), 64)
        snapshot = ReplayRunController(
            definition, runtime_root=Path(tempfile.gettempdir())
        ).snapshot()
        self.assertEqual(snapshot["debug_fixture"]["fixture_id"], "opening-range-case-1")

    def test_debug_fixture_rejects_records_outside_the_session(self) -> None:
        fixture = HistoricalDebugFixture(
            fixture_id="wrong-day",
            market_events=({
                "kind": "trade",
                "ticker": "AAPL",
                "ts": "2026-07-29T09:45:00-04:00",
                "price": 101.25,
            },),
        )
        with self.assertRaisesRegex(ValueError, "inside the configured session"):
            ReplayRunDefinition(
                session_date=date(2026, 7, 28),
                start_time=time(9, 45),
                mode=RunMode.BACKTEST_DEBUG,
                debug_fixture=fixture,
                configuration_revision=approved_configuration(),
            )

    def test_debug_fixture_hashes_and_projects_exact_watchlist_membership(self) -> None:
        fixture = HistoricalDebugFixture(
            fixture_id="eligible-aapl",
            market_events=({
                "kind": "trade",
                "ticker": "AAPL",
                "ts": "2026-07-28T09:45:01-04:00",
                "price": 101.25,
            },),
            watchlist_events=({
                "effective_at": "2026-07-28T09:45:00-04:00",
                "event": "added",
                "ibkr_conid": 265598,
                "ticker": "AAPL",
                "watchlist_id": "squeeze-tradable-candidates",
            },),
        )

        timeline = _debug_watchlist_membership_timeline(fixture.watchlist_events)

        self.assertEqual(fixture.payload()["watchlist_event_count"], 1)
        self.assertEqual(timeline[0]["members"][0]["ticker"], "AAPL")
        self.assertEqual(timeline[0]["members"][0]["ibkr_conid"], 265598)
        self.assertEqual(
            timeline[0]["members"][0]["watchlist_ids"],
            ["squeeze-tradable-candidates"],
        )

    def test_debug_fixture_rejects_unresolved_watchlist_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive point-in-time conid"):
            HistoricalDebugFixture(
                fixture_id="unresolved-identity",
                market_events=({
                    "kind": "trade",
                    "ticker": "AAPL",
                    "ts": "2026-07-28T09:45:01-04:00",
                    "price": 101.25,
                },),
                watchlist_events=({
                    "effective_at": "2026-07-28T09:45:00-04:00",
                    "event": "added",
                    "ticker": "AAPL",
                    "watchlist_id": "squeeze-tradable-candidates",
                },),
            )


class HistoricalDebugFixtureTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self) -> HistoricalDebugFixture:
        return HistoricalDebugFixture(
            fixture_id="deterministic-aapl",
            market_events=(
                {
                    "kind": "quote",
                    "ticker": "AAPL",
                    "ts": "2026-07-28T09:45:00-04:00",
                    "sequence": 1,
                    "bid_price": 101.2,
                    "ask_price": 101.3,
                    "bid_size": 20,
                    "ask_size": 30,
                },
                {
                    "kind": "trade",
                    "ticker": "AAPL",
                    "ts": "2026-07-28T09:45:01-04:00",
                    "sequence": 2,
                    "price": 101.25,
                    "size": 100,
                },
            ),
            derived_frames=({
                "ticker": "AAPL",
                "timeframe": "1m",
                "as_of": "2026-07-28T09:45:01-04:00",
                "sequence": 3,
                "bar": {"close": 101.25},
                "indicator": {"vwap": 101.1},
            },),
        )

    def lifecycle_fixture(self) -> HistoricalDebugFixture:
        entry = {
            "close": 101.0,
            "previous_close": 100.0,
            "previous_high": 100.5,
            "structure_swing_high": 100.5,
            "structure_swing_low": 99.5,
            "vwap": 100.2,
            "macd_line": 0.4,
            "macd_signal": 0.2,
            "macd_histogram": 0.2,
            "flow_structure_composite_score": 0.6,
            "flow_structure_composite_confidence": 0.8,
            "flow_structure_composite_bias": "bullish",
            "atr_14": 0.4,
            "structure_luld_upper": 110.0,
        }
        strategic_exit = {
            **entry,
            "close": 100.0,
            "vwap": 100.1,
        }
        return HistoricalDebugFixture(
            fixture_id="strategy-round-trip-aapl",
            market_events=(
                {
                    "kind": "quote",
                    "ticker": "AAPL",
                    "ts": "2026-07-28T09:45:00-04:00",
                    "sequence": 1,
                    "bid_price": 100.99,
                    "ask_price": 101.01,
                    "bid_size": 1_000,
                    "ask_size": 1_000,
                },
                {
                    "kind": "trade",
                    "ticker": "AAPL",
                    "ts": "2026-07-28T09:45:02-04:00",
                    "sequence": 2,
                    "price": 101.0,
                    "size": 1_000,
                },
                {
                    "kind": "quote",
                    "ticker": "AAPL",
                    "ts": "2026-07-28T09:45:03-04:00",
                    "sequence": 3,
                    "bid_price": 100.8,
                    "ask_price": 100.82,
                    "bid_size": 1_000,
                    "ask_size": 1_000,
                },
                {
                    "kind": "trade",
                    "ticker": "AAPL",
                    "ts": "2026-07-28T09:45:05-04:00",
                    "sequence": 4,
                    "price": 100.0,
                    "size": 1_000,
                },
            ),
            derived_frames=(
                {
                    "ticker": "AAPL",
                    "timeframe": "100ms",
                    "as_of": "2026-07-28T09:45:00.500-04:00",
                    "sequence": 1,
                    "bar": {"close": 101.0},
                    "indicator": entry,
                },
                {
                    "ticker": "AAPL",
                    "timeframe": "5s",
                    "as_of": "2026-07-28T09:45:00.750-04:00",
                    "sequence": 2,
                    "bar": {"close": 101.0},
                    "indicator": entry,
                },
                {
                    "ticker": "AAPL",
                    "timeframe": "1s",
                    "as_of": "2026-07-28T09:45:01-04:00",
                    "sequence": 3,
                    "bar": {"close": 101.0},
                    "indicator": entry,
                },
                {
                    "ticker": "AAPL",
                    "timeframe": "1s",
                    "as_of": "2026-07-28T09:45:04-04:00",
                    "sequence": 4,
                    "bar": {"close": 100.0},
                    "indicator": strategic_exit,
                },
            ),
        )

    def test_parses_canonical_events_and_frames_without_qmd(self) -> None:
        fixture = self.fixture()
        events = _debug_market_events(fixture.market_events)
        frames = _debug_derived_frames(fixture.derived_frames)

        self.assertEqual([event.kind for event in events], ["quote", "trade"])
        self.assertEqual(events[0].source, "debug_fixture:deterministic")
        self.assertEqual(frames[0].indicator["vwap"], 101.1)

    def test_debug_constructor_does_not_compile_historical_data_plans(self) -> None:
        with patch(
            "src.backend.replay_run_service._historical_watchlist_plans_for_configuration",
            side_effect=AssertionError("Debug must use fixture membership"),
        ), patch(
            "src.backend.replay_run_service._historical_core_signal_plans_for_configuration",
            side_effect=AssertionError("Debug must use fixture signal events"),
        ):
            controller = ReplayRunController(
                ReplayRunDefinition(
                    session_date=date(2026, 7, 28),
                    start_time=time(9, 45),
                    mode=RunMode.BACKTEST_DEBUG,
                    debug_fixture=self.fixture(),
                    configuration_revision=approved_configuration(),
                ),
                runtime_root=Path(tempfile.gettempdir()),
            )

        self.assertEqual(controller._historical_watchlist_plans, [])
        self.assertEqual(controller._historical_core_signal_plans, [])
        self.assertFalse(controller._bar_gpt_fields_required())

    async def test_controller_injects_fixture_batches_instead_of_qmd(self) -> None:
        definition = ReplayRunDefinition(
            session_date=date(2026, 7, 28),
            start_time=time(9, 45),
            mode=RunMode.BACKTEST_DEBUG,
            debug_fixture=self.fixture(),
            configuration_revision=approved_configuration(),
        )
        controller = ReplayRunController(definition, runtime_root=Path(tempfile.gettempdir()))
        batches = [batch async for batch in controller._market_event_batches()]

        self.assertEqual(len(batches), 1)
        self.assertEqual([event.sequence for event in batches[0]], [1, 2])

    async def test_manifest_persists_exact_fixture_records_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            definition = ReplayRunDefinition(
                session_date=date(2026, 7, 28),
                start_time=time(9, 45),
                mode=RunMode.BACKTEST_DEBUG,
                tickers=("AAPL",),
                debug_fixture=self.fixture(),
                configuration_revision=approved_configuration(),
            )
            controller = ReplayRunController(definition, runtime_root=Path(directory))
            controller.run_dir.mkdir(parents=True)
            controller._write_approved_configuration()
            controller._write_manifest()
            fixture_payload = json.loads(
                (controller.run_dir / "debug-fixture.json").read_text(encoding="utf-8")
            )
            manifest = json.loads(
                (controller.run_dir / "manifest.json").read_text(encoding="utf-8")
            )
            summary_exists = (controller.run_dir / "run-summary.json").is_file()

        self.assertEqual(fixture_payload["fixture_id"], "deterministic-aapl")
        self.assertEqual(fixture_payload["market_event_count"], 2)
        self.assertEqual(fixture_payload["content_hash"], self.fixture().content_hash)
        self.assertEqual(manifest["schema_version"], 2)
        self.assertNotIn("approved_configuration", manifest)
        self.assertEqual(
            manifest["approved_configuration_path"],
            "approved-configuration.json",
        )
        self.assertTrue(summary_exists)
        authority = manifest["run"]["data_authority"]["sources"]["market_events"]
        self.assertEqual(authority["authority"], "backtest_debug_fixture")
        self.assertEqual(authority["revision_token"], self.fixture().content_hash)

    async def test_debug_fixture_completes_through_shared_runtime_without_qmd(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.backend.replay_run_service.QmdHistoricalEventSource"
        ) as qmd_source:
            definition = ReplayRunDefinition(
                session_date=date(2026, 7, 28),
                start_time=time(9, 45),
                mode=RunMode.BACKTEST_DEBUG,
                tickers=("AAPL",),
                debug_fixture=self.fixture(),
                configuration_revision=approved_configuration(),
            )
            controller = ReplayRunController(definition, runtime_root=Path(directory))
            await controller.start()
            assert controller._task is not None
            await controller._task
            try:
                self.assertEqual(controller.status, "completed", controller.error)
                self.assertEqual(controller.processed_events, 2)
                self.assertEqual(controller.current_time.isoformat(), "2026-07-28T09:45:01-04:00")
                self.assertIsNotNone(controller._journal.load_checkpoint(controller.run_id))
                checkpoint = controller.snapshot()["checkpoint"]
                self.assertEqual(checkpoint["status"], "available")
                self.assertEqual(checkpoint["processed_events"], 2)
                self.assertTrue(checkpoint["resume_supported"])
                qmd_source.assert_not_called()
            finally:
                if controller._journal is not None:
                    controller._journal.close()

    async def test_debug_fixture_runs_strategy_round_trip_to_terminal_flat_state(self) -> None:
        assignment = {
            "assignment_id": "round-trip-aapl",
            "account_key": "primary",
            "ticker": "AAPL",
            "conid": 265598,
            "status": "watching",
            "permissions": {
                "observe": True,
                "enter": True,
                "add": True,
                "reduce": True,
                "exit": True,
                "reenter": True,
            },
            "parameters": default_long_momentum_parameters(),
        }
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.backend.replay_run_service.QmdHistoricalEventSource"
        ) as qmd_source:
            controller = ReplayRunController(
                ReplayRunDefinition(
                    session_date=date(2026, 7, 28),
                    start_time=time(9, 45),
                    mode=RunMode.BACKTEST_DEBUG,
                    tickers=("AAPL",),
                    debug_fixture=self.lifecycle_fixture(),
                    configuration_revision=approved_configuration(
                        assignments=[assignment]
                    ),
                ),
                runtime_root=Path(directory),
            )
            await controller.start()
            assert controller._task is not None
            await controller._task
            try:
                journal = [
                    record.payload
                    for record in controller._journal.records(controller.run_id)
                ]
                self.assertEqual(controller.status, "completed", controller.error)
                payload = await controller.canvas_payload("AAPL")
                trading = payload["trading"]

                self.assertEqual(controller.processed_events, 4)
                self.assertGreaterEqual(len(trading["executions"]), 2)
                self.assertFalse(
                    [
                        row
                        for row in trading["positions"]
                        if float(row.get("quantity") or 0) != 0
                    ]
                )
                self.assertEqual(len(trading["closed_trades"]), 1)
                self.assertTrue(
                    any(
                        row.get("action") == "enter_long"
                        for row in payload["strategy"]["decisions"]
                    ),
                    payload["strategy"]["decisions"],
                )
                self.assertEqual(
                    trading["closed_trades"][0]["strategy_id"],
                    STRATEGY_ID,
                )
                self.assertEqual(
                    trading["closed_trades"][0]["run_id"],
                    controller.run_id,
                )
                self.assertTrue(
                    any(row.get("action") == "enter_long" for row in journal),
                    journal,
                )
                self.assertTrue(
                    any(
                        row.get("action") == "exit"
                        and row.get("reason") == "failed_breakout"
                        for row in journal
                    ),
                    journal,
                )
                self.assertIsNotNone(controller._journal.load_checkpoint(controller.run_id))
                qmd_source.assert_not_called()
            finally:
                if controller._journal is not None:
                    controller._journal.close()

    async def test_debug_frame_projects_price_acceleration_into_profit_management(self) -> None:
        assignment = {
            "assignment_id": "acceleration-aapl",
            "account_key": "primary",
            "ticker": "AAPL",
            "conid": 265598,
            "status": "watching",
            "permissions": {
                "observe": True,
                "enter": True,
                "add": True,
                "reduce": True,
                "exit": True,
                "reenter": True,
            },
            "parameters": default_long_momentum_parameters(),
        }
        fixture = HistoricalDebugFixture(
            fixture_id="profit-slowdown-aapl",
            market_events=({
                "kind": "quote",
                "ticker": "AAPL",
                "ts": "2026-07-28T09:45:00-04:00",
                "bid_price": 101.99,
                "ask_price": 102.01,
            },),
            derived_frames=({
                "ticker": "AAPL",
                "timeframe": "1s",
                "as_of": "2026-07-28T09:45:01-04:00",
                "sequence": 1,
                "bar": {"close": 102.0},
                "indicator": {
                    "close": 102.0,
                    "vwap": 101.5,
                    "price_change_1_bar_pct": 0.1,
                },
            },),
        )
        with tempfile.TemporaryDirectory() as directory:
            controller = ReplayRunController(
                ReplayRunDefinition(
                    session_date=date(2026, 7, 28),
                    start_time=time(9, 45),
                    mode=RunMode.BACKTEST_DEBUG,
                    tickers=("AAPL",),
                    debug_fixture=fixture,
                    configuration_revision=approved_configuration(assignments=[assignment]),
                ),
                runtime_root=Path(directory),
            )
            controller._journal = TradingJournal(Path(directory) / "journal.sqlite3")
            await controller._initialize_runtime()
            assert controller._runtime is not None
            process = AsyncMock()
            controller._runtime.process_account_strategy_observation = process
            controller._stream_tickers = ("AAPL",)
            controller._quotes["AAPL"] = _debug_market_events(
                fixture.market_events
            )[0]
            try:
                await controller._process_strategy_frame(
                    _debug_derived_frames(fixture.derived_frames)[0]
                )
                observation = process.await_args.args[0]
                self.assertEqual(observation.acceleration, 0.1)
            finally:
                if controller._journal is not None:
                    controller._journal.close()

    async def test_service_restores_complete_debug_checkpoint(self) -> None:
        class StopAfterFirstEvent(ReplayRunController):
            async def _after_event(self, event_time: datetime) -> None:
                await super()._after_event(event_time)
                if self.processed_events == 1:
                    self._stop_requested = True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            definition = ReplayRunDefinition(
                session_date=date(2026, 7, 28),
                start_time=time(9, 45),
                mode=RunMode.BACKTEST_DEBUG,
                tickers=("AAPL",),
                debug_fixture=self.fixture(),
                configuration_revision=approved_configuration(),
            )
            controller = StopAfterFirstEvent(definition, runtime_root=root)
            await controller.start()
            assert controller._task is not None
            await controller._task
            if controller._journal is not None:
                controller._journal.close()
            self.assertEqual(controller.status, "stopped")
            self.assertEqual(controller.processed_events, 1)

            service = ReplayRunService(runtime_root=root)
            self.assertEqual(service.list(), [])
            manifest_path = controller.run_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            approved_path = controller.run_dir / "approved-configuration.json"
            approved = json.loads(approved_path.read_text(encoding="utf-8"))
            approved["content_hash"] = "changed"
            approved_path.write_text(json.dumps(approved), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "identity changed"):
                await service.resume(controller.run_id)
            approved["content_hash"] = "test-hash"
            approved_path.write_text(json.dumps(approved), encoding="utf-8")
            resumed = await service.resume(controller.run_id)
            assert resumed._task is not None
            await resumed._task
            try:
                self.assertEqual(resumed.status, "completed", resumed.error)
                self.assertEqual(resumed.processed_events, 2)
                self.assertEqual(
                    resumed.current_time.isoformat(),
                    "2026-07-28T09:45:01-04:00",
                )
                self.assertTrue(resumed.snapshot()["checkpoint"]["resume_supported"])
            finally:
                if resumed._journal is not None:
                    resumed._journal.close()

    async def test_derived_only_debug_run_writes_restart_safe_cursor(self) -> None:
        fixture = HistoricalDebugFixture(
            fixture_id="derived-only",
            derived_frames=(
                {
                    "ticker": "AAPL",
                    "timeframe": "1m",
                    "as_of": "2026-07-28T09:45:01-04:00",
                    "sequence": 1,
                    "bar": {"close": 101.25},
                    "indicator": {"vwap": 101.1},
                },
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            controller = ReplayRunController(
                ReplayRunDefinition(
                    session_date=date(2026, 7, 28),
                    start_time=time(9, 45),
                    mode=RunMode.BACKTEST_DEBUG,
                    tickers=("AAPL",),
                    debug_fixture=fixture,
                    configuration_revision=approved_configuration(),
                ),
                runtime_root=Path(directory),
            )
            await controller.start()
            assert controller._task is not None
            await controller._task
            try:
                checkpoint = controller.snapshot()["checkpoint"]
                self.assertTrue(checkpoint["resume_supported"])
                persisted = controller._journal.load_checkpoint(controller.run_id)
                self.assertEqual(
                    persisted["state"]["controller"]["frame_cursor"]["sequence"],
                    1,
                )
                self.assertEqual(
                    persisted["state"]["controller"]["source_cursor"], {}
                )
            finally:
                if controller._journal is not None:
                    controller._journal.close()

    def test_debug_preflight_uses_configuration_and_external_storage_not_qmd(self) -> None:
        configuration = approved_configuration()
        configuration["payload"]["run_plan"] = {
            "activation": {"watchlist_policy": "any_selected"},
            "watchlist_ids": ["squeeze-tradable-candidates"],
        }
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.backend.replay_run_service.backtest_debug_runtime_root",
            return_value=Path(directory),
        ), patch(
            "src.backend.replay_run_service.historical_preflight"
        ) as historical:
            payload = backtest_debug_preflight(
                session_date=date(2026, 7, 28),
                start_time=time(9, 45),
                tickers=("AAPL",),
                configuration_revision=configuration,
            )

        self.assertTrue(payload["ready"])
        self.assertEqual(payload["configuration_revision_id"], "configuration-test")
        self.assertEqual(payload["required_watchlist_ids"], ["squeeze-tradable-candidates"])
        self.assertEqual(payload["watchlist_policy"], "any_selected")
        historical.assert_not_called()


class HistoricalWatchlistTimelineTests(unittest.TestCase):
    def test_premarket_strategy_indicator_horizon_ends_after_flatten_clock(self) -> None:
        start = datetime(2026, 8, 21, 4, 0, tzinfo=NEW_YORK)
        end = datetime(2026, 8, 21, 20, 0, tzinfo=NEW_YORK)
        configuration = {
            "strategy_profile": {
                "lifecycle": {
                    "trading_behavior": {
                        "eligible_sessions": ["premarket"],
                        "flatten_time": "09:29:59",
                    }
                }
            }
        }

        self.assertEqual(
            _strategy_evaluation_end(
                configuration, session_start=start, session_end=end
            ),
            datetime(2026, 8, 21, 9, 30, tzinfo=NEW_YORK),
        )
        self.assertEqual(
            _strategy_evaluation_end(
                {}, session_start=start, session_end=end
            ),
            end,
        )

    def test_source_native_watchlist_evaluates_only_at_occurrence_clocks(self) -> None:
        plan = {
            "watchlist_id": "squeeze-tradable-candidates",
            "start": "2026-08-21T08:00:00+00:00",
            "end": "2026-08-21T20:00:00+00:00",
            "cadence_ms": 1_000,
            "evaluation_windows": [{
                "start": "2026-08-21T08:00:00+00:00",
                "end": "2026-08-21T20:00:00+00:00",
            }],
            "plan_hash": "sha256:full-session",
        }
        events = [
            ReplaySignalEvent(
                available_at=datetime(2026, 8, 21, 8, minute, second, tzinfo=ZoneInfo("UTC")),
                occurrence={"event_id": f"event-{minute}-{second}"},
                source_values={},
                ticker=ticker,
            )
            for minute, second, ticker in ((1, 5, "AAPL"), (3, 7, "MSFT"))
        ]
        configuration = {
            "signal_activation": {
                "signal_streams": [{
                    "enabled": True,
                    "occurrence_source": "qmd_squeeze_episode",
                }]
            }
        }

        scoped = _historical_watchlist_plans_at_source_native_events(
            [plan],
            events,
            configuration=configuration,
        )

        self.assertEqual(
            scoped[0]["evaluation_windows"],
            [
                {
                    "start": "2026-08-21T08:01:05+00:00",
                    "end": "2026-08-21T08:01:06+00:00",
                },
                {
                    "start": "2026-08-21T08:03:07+00:00",
                    "end": "2026-08-21T08:03:08+00:00",
                },
            ],
        )
        self.assertNotEqual(scoped[0]["plan_hash"], plan["plan_hash"])
        self.assertEqual(plan["evaluation_windows"][0]["end"], "2026-08-21T20:00:00+00:00")

    def test_source_native_watchlist_skips_clocks_where_strategy_cannot_enter(self) -> None:
        plan = {
            "watchlist_id": "squeeze-tradable-candidates",
            "start": "2026-08-21T08:00:00+00:00",
            "end": "2026-08-21T20:00:00+00:00",
            "cadence_ms": 1_000,
            "plan_hash": "sha256:full-session",
        }
        events = [
            ReplaySignalEvent(
                available_at=available_at,
                occurrence={"event_id": ticker},
                source_values={},
                ticker=ticker,
            )
            for ticker, available_at in (
                ("PRE", datetime(2026, 8, 21, 9, 15, tzinfo=NEW_YORK)),
                ("RTH", datetime(2026, 8, 21, 10, 0, tzinfo=NEW_YORK)),
            )
        ]
        configuration = {
            "signal_activation": {
                "signal_streams": [{
                    "enabled": True,
                    "occurrence_source": "qmd_squeeze_episode",
                }]
            },
            "strategy_profile": {
                "lifecycle": {
                    "trading_behavior": {
                        "eligible_sessions": ["premarket"],
                        "entry_cutoff_time": "09:29:59",
                    }
                }
            },
        }

        scoped = _historical_watchlist_plans_at_source_native_events(
            [plan], events, configuration=configuration
        )

        self.assertEqual(
            scoped[0]["evaluation_windows"],
            [{
                "start": "2026-08-21T13:15:00+00:00",
                "end": "2026-08-21T13:15:01+00:00",
            }],
        )

    def test_source_native_squeeze_occurrence_projects_strategy_trigger_aliases(self) -> None:
        values = _occurrence_source_values(
            {
                "available_at": "2026-08-21T10:30:18.507000+00:00",
                "squeeze_move_pct": 5.8149,
            }
        )

        self.assertEqual(values["signal.squeeze_move_pct"]["value"], 5.8149)
        self.assertEqual(
            values["data.signal.squeeze_move_pct@1:value"]["value"], 5.8149
        )

    @patch(
        "src.backend.historical_watchlist_feature_service.materialize_historical_watchlist_plans"
    )
    def test_projects_qmd_transition_chunks_into_causal_membership_snapshots(
        self, materialize
    ) -> None:
        materialize.return_value = {
            "batch_materialization_id": "sha256:batch",
            "materializations": [
                {
                    "watchlist_id": "core-candidates",
                    "plan_hash": "sha256:plan",
                    "materialization_id": "sha256:materialized",
                    "projection_complete": True,
                    "projection_mode": "full",
                    "projection_tickers": [],
                    "calculation_revision": "qmd-v3",
                    "source_revision": {"token": "events-v1"},
                    "external_feature_revisions": [],
                    "chunks": [{"transitions": [
                        {
                            "effective_at": "2026-08-10T13:30:00+00:00",
                            "event": "added",
                            "ticker": "AAPL",
                            "rank": 1,
                            "score": 20.0,
                            "reason": "rules passed",
                            "evidence": {"market.volume": 1000},
                            "identity": {"ibkr_conid": 265598},
                        },
                        {
                            "effective_at": "2026-08-10T13:31:00+00:00",
                            "event": "removed",
                            "ticker": "AAPL",
                        },
                        {
                            "effective_at": "2026-08-10T13:31:00+00:00",
                            "event": "added",
                            "ticker": "MSFT",
                            "rank": 1,
                            "score": 30.0,
                            "reason": "rules passed",
                            "evidence": {},
                            "identity": {"ibkr_conid": 272093},
                        },
                    ]}]
                }
            ],
        }

        timeline = _historical_watchlist_membership_timeline_from_plans(
            [{"watchlist_id": "core-candidates", "plan_hash": "sha256:plan"}]
        )

        self.assertEqual(
            [
                [row["ticker"] for row in item["transitions"]]
                for item in timeline
            ],
            [["AAPL"], ["AAPL", "MSFT"]],
        )
        self.assertEqual(
            timeline[0]["transitions"][0]["evidence"]["market.volume"],
            1000,
        )
        self.assertEqual(
            timeline[0]["transitions"][0]["identity"]["ibkr_conid"],
            265598,
        )
        self.assertEqual(
            timeline[0]["authority"][0]["materialization_id"],
            "sha256:materialized",
        )

    @patch(
        "src.backend.historical_watchlist_feature_service.materialize_historical_watchlist_plans"
    )
    def test_unions_multiple_watchlists_without_removing_shared_members(
        self, materialize
    ) -> None:
        def row(watchlist_id, transitions):
            return {
                "watchlist_id": watchlist_id,
                "plan_hash": f"sha256:{watchlist_id}",
                "materialization_id": f"sha256:m-{watchlist_id}",
                "chunks": [{"transitions": transitions}],
            }

        identity = {"ibkr_conid": 265598}
        materialize.return_value = {
            "batch_materialization_id": "sha256:batch",
            "materializations": [
                row("one", [
                    {"effective_at": "2026-08-10T13:30:00+00:00", "event": "added",
                     "ticker": "AAPL", "rank": 1, "identity": identity},
                    {"effective_at": "2026-08-10T13:32:00+00:00", "event": "removed",
                     "ticker": "AAPL"},
                ]),
                row("two", [
                    {"effective_at": "2026-08-10T13:31:00+00:00", "event": "added",
                     "ticker": "AAPL", "rank": 1, "identity": identity},
                ]),
            ],
        }

        timeline = _historical_watchlist_membership_timeline_from_plans([
            {"watchlist_id": "one", "plan_hash": "sha256:one"},
            {"watchlist_id": "two", "plan_hash": "sha256:two"},
        ])

        self.assertEqual([len(item["transitions"]) for item in timeline], [1, 1, 1])
        self.assertEqual(timeline[-1]["transitions"][0]["watchlist_id"], "one")
        self.assertEqual(len(timeline[-1]["authority"]), 2)

        definition = ReplayRunDefinition(
            session_date=date(2026, 8, 10),
            start_time=time(9, 30),
            mode=RunMode.BACKTEST,
            configuration_revision=approved_configuration(),
        )
        with tempfile.TemporaryDirectory() as directory:
            controller = ReplayRunController(definition, runtime_root=Path(directory))
            controller._historical_watchlist_timeline_cache = timeline
            controller._apply_historical_watchlist_membership(
                datetime(2026, 8, 10, 13, 32, tzinfo=ZoneInfo("UTC"))
            )
            self.assertEqual(controller._active_historical_watchlist_tickers, {"AAPL"})
            self.assertEqual(controller._active_historical_watchlists["two"], {"AAPL"})
            self.assertEqual(controller._active_historical_watchlists["one"], set())
            controller._runtime_inputs_ready = True
            runtime = controller.snapshot()["watchlist_runtime"]
            self.assertEqual(runtime["status"], "ready")
            projected = {row["watchlist_id"]: row for row in runtime["watchlists"]}
            self.assertEqual(projected["one"]["members"], [])
            self.assertEqual(projected["two"]["members"][0]["ticker"], "AAPL")
            self.assertEqual(projected["two"]["members"][0]["ibkr_conid"], 265598)

    def test_resolves_first_clock_and_each_later_weekday_session_boundary(self) -> None:
        approved = approved_configuration()
        with patch(
            "src.backend.replay_run_service._historical_watchlist_resolution_for_configuration",
            side_effect=lambda _approved, *, as_of: ([{
                "ticker": as_of.strftime("D%d"),
                "ibkr_conid": as_of.day,
            }], [{"scanner": {"source_revision": f"revision-{as_of.day}"}}]),
        ) as resolver:
            timeline = _historical_watchlist_membership_timeline_for_configuration(
                approved,
                start=datetime(2026, 8, 7, 9, 45, tzinfo=NEW_YORK),
                end=datetime(2026, 8, 11, 20, 0, tzinfo=NEW_YORK),
            )

        self.assertEqual(
            [row["effective_at"].isoformat() for row in timeline],
            [
                "2026-08-07T09:45:00-04:00",
                "2026-08-10T04:00:00-04:00",
                "2026-08-11T04:00:00-04:00",
            ],
        )
        self.assertEqual(resolver.call_count, 3)
        self.assertEqual(
            timeline[0]["authority"][0]["scanner"]["source_revision"],
            "revision-7",
        )

    def test_controller_applies_ordered_membership_changes_and_journals_them(self) -> None:
        definition = ReplayRunDefinition(
            session_date=date(2026, 8, 10),
            final_session_date=date(2026, 8, 11),
            start_time=time(9, 45),
            mode=RunMode.BACKTEST,
            configuration_revision=approved_configuration(),
        )
        with tempfile.TemporaryDirectory() as directory:
            controller = ReplayRunController(definition, runtime_root=Path(directory))
            controller._historical_watchlist_timeline_cache = [
                {
                    "effective_at": datetime(2026, 8, 10, 9, 45, tzinfo=NEW_YORK),
                    "members": [{
                        "ticker": "AAPL",
                        "ibkr_conid": 1,
                        "market.session_dollar_volume": 750_000.0,
                        "market.trade_rate_10s": 2.5,
                        "market.liquidity_score": 72.0,
                        "volume_rate_ratio": 2.0,
                        "volume_rate_ratio@@1s": 2.0,
                        "market.spread_bps": 18.0,
                    }],
                    "authority": [{
                        "watchlist_id": "small",
                        "scanner": {"source_revision": "archive:revision-17"},
                    }],
                },
                {
                    "effective_at": datetime(2026, 8, 11, 4, 0, tzinfo=NEW_YORK),
                    "members": [{"ticker": "MSFT", "ibkr_conid": 2}],
                },
            ]
            controller._journal = TradingJournal(Path(directory) / "journal.sqlite3")
            controller._record_historical_watchlist_authority()

            controller._apply_historical_watchlist_membership(
                datetime(2026, 8, 10, 10, 0, tzinfo=NEW_YORK)
            )
            self.assertEqual(controller._active_historical_watchlist_tickers, {"AAPL"})
            self.assertEqual(
                controller._strategy_source_values["AAPL"][
                    "market.session_dollar_volume"
                ]["value"],
                750_000.0,
            )
            self.assertEqual(
                controller._strategy_source_values["AAPL"]["market.spread_bps"][
                    "observed_at"
                ],
                "2026-08-10T09:45:00-04:00",
            )
            self.assertEqual(
                controller._strategy_source_values["AAPL"]["volume_rate_ratio@1s"][
                    "value"
                ],
                2.0,
            )
            controller._apply_historical_watchlist_membership(
                datetime(2026, 8, 11, 4, 1, tzinfo=NEW_YORK)
            )

            self.assertEqual(controller._active_historical_watchlist_tickers, {"MSFT"})
            self.assertNotIn("AAPL", controller._active_historical_watchlist_evidence)
            events = [
                record.payload["event"]
                for record in controller._journal.watchlist_membership_records()
            ]
            self.assertEqual(events, ["added", "added", "removed"])
            authority = controller.snapshot()["data_authority"]["sources"]
            self.assertIn("watchlist_membership_timeline", authority)
            self.assertEqual(
                authority["watchlist_membership_timeline"]["snapshot_count"], 2
            )
            authority_records = controller._journal.recent_records(
                controller.run_id,
                categories=("data_authority",),
                limit=10,
            )
            self.assertEqual(len(authority_records), 1)
            controller._journal.close()


class ReplayRunServiceCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_evicts_only_the_oldest_terminal_resident_run(self) -> None:
        service = ReplayRunService(
            runtime_root=Path(tempfile.gettempdir()),
            max_resident_runs=2,
        )
        controllers = [
            MagicMock(
                run_id=f"00000000-0000-0000-0000-00000000000{index}",
                status=status,
                updated_at=datetime(2026, 8, 10, index, tzinfo=NEW_YORK),
                start=AsyncMock(),
            )
            for index, status in ((1, "completed"), (2, "running"), (3, "created"))
        ]
        with patch(
            "src.backend.replay_run_service.ReplayRunController",
            side_effect=controllers,
        ):
            await service.create(MagicMock())
            await service.create(MagicMock())
            await service.create(MagicMock())

        with self.assertRaises(KeyError):
            service.get(controllers[0].run_id)
        self.assertIs(service.get(controllers[1].run_id), controllers[1])
        self.assertIs(service.get(controllers[2].run_id), controllers[2])

    async def test_rejects_new_run_when_every_resident_run_is_active(self) -> None:
        service = ReplayRunService(
            runtime_root=Path(tempfile.gettempdir()),
            max_resident_runs=1,
        )
        first = MagicMock(
            run_id="00000000-0000-0000-0000-000000000001",
            status="running",
            updated_at=datetime(2026, 8, 10, tzinfo=NEW_YORK),
            start=AsyncMock(),
        )
        second = MagicMock(
            run_id="00000000-0000-0000-0000-000000000002",
            status="created",
            updated_at=datetime(2026, 8, 10, tzinfo=NEW_YORK),
            start=AsyncMock(),
        )
        with patch(
            "src.backend.replay_run_service.ReplayRunController",
            side_effect=[first, second],
        ):
            await service.create(MagicMock())
            with self.assertRaises(ReplayRunCapacityError):
                await service.create(MagicMock())

        second.start.assert_not_awaited()

    async def test_replay_route_returns_typed_capacity_response(self) -> None:
        from fastapi import HTTPException
        from src.backend import app as backend_app

        request = backend_app.ReplayRunCreateRequest(
            session_date=date(2026, 8, 10),
            configuration_revision_id="configuration-test",
        )
        definition = MagicMock(
            session_date=request.session_date,
            start_time=time(9, 45),
            initial_cash=request.initial_cash,
            assignment_ids=(),
            tickers=(),
        )
        with (
            patch.object(
                backend_app,
                "replay_configuration_snapshot",
                return_value={"revision_id": "configuration-test"},
            ),
            patch.object(backend_app, "ReplayRunDefinition", return_value=definition),
            patch.object(backend_app, "replay_preflight", return_value={"ready": True}),
            patch.object(
                backend_app.replay_run_service,
                "create",
                AsyncMock(side_effect=ReplayRunCapacityError("capacity full")),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await backend_app.trading_replay_run_create(request)

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.detail, "capacity full")


class ReplayHistoricalFetchBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_resource_and_transport_stream_failures_are_retryable(self) -> None:
        self.assertTrue(
            _retryable_historical_stream_error(
                RuntimeError(
                    "QMD derived stream failed: historical cache byte limit exceeded"
                )
            )
        )
        self.assertTrue(
            _retryable_historical_stream_error(
                RuntimeError("QMD derived stream closed early for ABCD 100ms")
            )
        )
        self.assertFalse(
            _retryable_historical_stream_error(
                RuntimeError("invalid focused repair coverage row")
            )
        )

    async def test_default_history_fetch_concurrency_matches_gateway_build_budget(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(replay_history_fetch_concurrency(), 4)

    async def test_frame_spool_orders_frames_and_attaches_causal_signals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            spool = ReplayFrameSpool(Path(directory) / "frames.sqlite3")
            later = datetime(2026, 8, 10, 9, 0, 2, tzinfo=ZoneInfo("UTC"))
            earlier = datetime(2026, 8, 10, 9, 0, 1, tzinfo=ZoneInfo("UTC"))
            spool.append([
                ReplayDerivedFrame(later, {"close": 11.0}, {}, 2, "ABCD", "1s"),
                ReplayDerivedFrame(earlier, {"close": 10.0}, {}, 1, "ABCD", "1s"),
            ])
            spool.finalize({
                "ABCD": [{
                    "effective_at": earlier.isoformat(),
                    "signal_key": "breakout",
                    "working_timeframe": "1s",
                    "state": "active",
                    "score": 0.8,
                }],
            })

            frames = list(spool)

        self.assertEqual([frame.as_of for frame in frames], [earlier, later])
        self.assertEqual(frames[0].signals, {"breakout@1s": 0.8})

    async def test_frame_spool_can_reopen_completed_resume_data_without_resetting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.sqlite3"
            observed_at = datetime(2026, 8, 10, 9, 0, 1, tzinfo=ZoneInfo("UTC"))
            spool = ReplayFrameSpool(path)
            spool.append([
                ReplayDerivedFrame(
                    observed_at,
                    {"close": 10.0},
                    {"vwap": 9.9},
                    1,
                    "ABCD",
                    "1s",
                ),
            ])
            spool.mark_stream_complete("ABCD", "1s")
            spool.finalize({})

            reopened = ReplayFrameSpool(path, reset=False)
            reopened.finalize({})
            frames = list(reopened)
            completed = reopened.completed_streams()

        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].ticker, "ABCD")
        self.assertEqual(frames[0].indicator["vwap"], 9.9)
        self.assertEqual(completed, {("ABCD", "1s")})

    async def test_frame_spool_does_not_claim_an_interrupted_stream_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            spool = ReplayFrameSpool(Path(directory) / "frames.sqlite3")
            spool.append([
                ReplayDerivedFrame(
                    datetime(2026, 8, 10, 9, 0, 1, tzinfo=ZoneInfo("UTC")),
                    {"close": 10.0},
                    {"vwap": 9.9},
                    1,
                    "ABCD",
                    "1s",
                ),
            ])

            completed = spool.completed_streams()

        self.assertEqual(completed, set())

    async def test_shared_historical_frame_cache_reuses_frozen_causal_tape(self) -> None:
        start = datetime(2026, 8, 10, 8, tzinfo=ZoneInfo("UTC"))
        end = datetime(2026, 8, 10, 9, tzinfo=ZoneInfo("UTC"))
        frame = ReplayDerivedFrame(
            as_of=start,
            bar={"close": 10.0},
            indicator={"flow_structure_composite_score": 0.7},
            sequence=1,
            ticker="ABCD",
            timeframe="100ms",
        )
        authority = {
            "authority": "qmd_history_derived",
            "revision_token": "revision-1",
            "source_plan_hash": "plan-1",
            "complete_for_history": True,
            "source_tiers": ["archive"],
            "engine_version": "engine-1",
            "event_count": 10,
        }
        cache = {
            ("ABCD", "100ms", start.isoformat(), end.isoformat()): (
                (frame,),
                authority,
            )
        }
        observed: dict[str, dict] = {}

        frames = await _historical_derived_frames(
            ticker="ABCD",
            timeframe="100ms",
            start=start,
            end=end,
            authority_sink=observed.__setitem__,
            frame_cache=cache,
        )

        self.assertEqual(len(frames), 1)
        self.assertIsNot(frames[0], frame)
        self.assertIs(frames[0].bar, frame.bar)
        self.assertEqual(observed["derived:ABCD:100ms"], authority)

    async def test_prepared_frame_cache_survives_new_controller(self) -> None:
        configuration = approved_configuration()
        configuration["payload"]["signal_activation"] = {
            "signal_streams": [{
                "signal_stream_id": "price-squeeze-early",
                "enabled": True,
                "occurrence_source": "qmd_squeeze_episode",
            }]
        }
        definition = ReplayRunDefinition(
            session_date=date(2026, 8, 10),
            start_time=time(4, 0),
            tickers=("ABCD",),
            configuration_revision=configuration,
        )
        frame = ReplayDerivedFrame(
            as_of=definition.session_start,
            bar={"close": 10.0},
            indicator={"vwap": 9.9},
            sequence=1,
            ticker="ABCD",
            timeframe="100ms",
        )
        authority = {
            "authority": "qmd_history_derived",
            "revision_token": "revision-1",
            "source_plan_hash": "plan-1",
            "complete_for_history": True,
            "source_tiers": ["archive"],
            "engine_version": "engine-1",
            "event_count": 10,
        }
        source_revision = {
            "token": "session-revision-1",
            "source_plan_hash": "session-plan-1",
            "complete_for_history": True,
            "request_complete": True,
            "source_tiers": ["archive"],
        }

        async def derived(**kwargs):
            await kwargs["frame_sink"]([frame])
            kwargs["authority_sink"](
                "derived:ABCD:100ms", deepcopy(authority)
            )

        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory)

            def controller() -> ReplayRunController:
                result = ReplayRunController(definition, runtime_root=runtime_root)
                result._strategy = MagicMock()
                result._strategy.assignments.return_value = [
                    MagicMock(ticker="ABCD", parameters={})
                ]
                result._strategy_registration = MagicMock()
                result._strategy_registration.timeframe_resolver.return_value = {
                    "100ms"
                }
                return result

            first = controller()
            second = controller()
            with (
                patch(
                    "src.backend.replay_run_service.qmd_historical_source_revision",
                    return_value=source_revision,
                ) as source_revision_fetch,
                patch(
                    "src.backend.replay_run_service._stream_historical_derived_frames",
                    side_effect=derived,
                ) as fetch,
            ):
                first_frames = list(await first._load_strategy_frames())
                second_frames = list(await second._load_strategy_frames())
                source_revision_fetch.return_value = {
                    **source_revision,
                    "token": "session-revision-2",
                }
                third = controller()
                third_frames = list(await third._load_strategy_frames())

            cache_files = list(
                (runtime_root / "_prepared" / "strategy-frames").glob("*.sqlite3")
            )

        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(len(first_frames), 1)
        self.assertEqual(len(second_frames), 1)
        self.assertEqual(len(third_frames), 1)
        self.assertEqual(
            second._data_authority["derived:ABCD:100ms"], authority
        )
        self.assertEqual(len(cache_files), 2)

    async def test_loads_scanner_signals_once_and_bounds_derived_fetches(self) -> None:
        definition = ReplayRunDefinition(
            session_date=date(2026, 8, 10),
            start_time=time(9, 45),
            tickers=tuple(f"T{index}" for index in range(12)),
            configuration_revision=approved_configuration(),
        )
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        controller = ReplayRunController(
            definition,
            runtime_root=Path(temporary_directory.name),
        )
        assignments = [
            MagicMock(ticker=f"T{index}", parameters={}) for index in range(12)
        ]
        controller._strategy = MagicMock()
        controller._strategy.assignments.return_value = assignments
        controller._strategy_registration = MagicMock()
        controller._strategy_registration.timeframe_resolver.return_value = {"1m"}
        active = 0
        maximum_active = 0

        async def derived(**kwargs):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return []

        signal_rows = {f"T{index}": [] for index in range(12)}
        with (
            patch(
                "src.backend.replay_run_service.qmd_historical_source_revision",
                return_value={
                    "token": "session-revision-1",
                    "source_plan_hash": "session-plan-1",
                    "complete_for_history": True,
                    "request_complete": True,
                    "source_tiers": ["archive"],
                },
            ),
            patch(
                "src.backend.replay_run_service._stream_historical_derived_frames",
                side_effect=derived,
            ) as derived_fetch,
            patch(
                "src.backend.replay_run_service._historical_signal_events",
                AsyncMock(return_value=signal_rows),
            ) as signal_fetch,
            patch.dict(
                "os.environ",
                {"TRADING_REPLAY_HISTORY_FETCH_CONCURRENCY": "8"},
            ),
        ):
            frames = await controller._load_strategy_frames()

        self.assertEqual(list(frames), [])
        self.assertEqual(derived_fetch.call_count, 12)
        self.assertLessEqual(maximum_active, 8)
        signal_fetch.assert_awaited_once_with(
            tickers=tuple(sorted(f"T{index}" for index in range(12))),
            start=definition.session_start,
            end=definition.session_end,
            authority_sink=controller._record_data_authority,
        )

    async def test_retries_closed_derived_stream_without_duplicate_partial_frames(self) -> None:
        definition = ReplayRunDefinition(
            session_date=date(2026, 8, 10),
            start_time=time(9, 45),
            tickers=("ABCD",),
            configuration_revision=approved_configuration(),
        )
        with tempfile.TemporaryDirectory() as directory:
            controller = ReplayRunController(definition, runtime_root=Path(directory))
            controller._strategy = MagicMock()
            controller._strategy.assignments.return_value = [
                MagicMock(ticker="ABCD", parameters={})
            ]
            controller._strategy_registration = MagicMock()
            controller._strategy_registration.timeframe_resolver.return_value = {"1m"}
            frame = ReplayDerivedFrame(
                as_of=definition.session_start,
                bar={"close": 10.0},
                indicator={},
                sequence=1,
                ticker="ABCD",
                timeframe="1m",
            )
            attempts = 0

            async def derived(**kwargs):
                nonlocal attempts
                attempts += 1
                await kwargs["frame_sink"]([frame])
                if attempts == 1:
                    raise RuntimeError(
                        "QMD derived stream failed: historical cache byte limit exceeded"
                    )

            with (
                patch(
                    "src.backend.replay_run_service.qmd_historical_source_revision",
                    return_value={
                        "token": "session-revision-1",
                        "source_plan_hash": "session-plan-1",
                        "complete_for_history": True,
                        "request_complete": True,
                        "source_tiers": ["archive"],
                    },
                ),
                patch(
                    "src.backend.replay_run_service._stream_historical_derived_frames",
                    side_effect=derived,
                ),
                patch(
                    "src.backend.replay_run_service._historical_signal_events",
                    AsyncMock(return_value={"ABCD": []}),
                ),
                patch("src.backend.replay_run_service.asyncio.sleep", AsyncMock()),
            ):
                frames = controller._load_strategy_frames()
                loaded = list(await frames)

        self.assertEqual(attempts, 2)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].ticker, "ABCD")

    async def test_groups_one_cross_sectional_signal_response_by_ticker(self) -> None:
        payload = MagicMock(
            payload={
                "recent_signal_events": [
                    {
                        "ticker": "AAPL",
                        "effective_at": "2026-08-10T15:00:02+00:00",
                    },
                    {
                        "ticker": "MSFT",
                        "effective_at": "2026-08-10T15:00:01+00:00",
                    },
                    {
                        "ticker": "AAPL",
                        "effective_at": "2026-08-10T15:00:01+00:00",
                    },
                    {
                        "ticker": "TSLA",
                        "effective_at": "2026-08-10T15:00:01+00:00",
                    },
                ]
            }
        )
        with patch(
            "src.backend.replay_run_service.qmd_product_request",
            return_value=payload,
        ) as request:
            grouped = await _historical_signal_events(
                tickers=("AAPL", "MSFT"),
                start=datetime(2026, 8, 10, 14, tzinfo=NEW_YORK),
                end=datetime(2026, 8, 10, 16, tzinfo=NEW_YORK),
            )

        request.assert_called_once()
        self.assertEqual(list(grouped), ["AAPL", "MSFT"])
        self.assertEqual(
            [row["effective_at"] for row in grouped["AAPL"]],
            [
                "2026-08-10T15:00:01+00:00",
                "2026-08-10T15:00:02+00:00",
            ],
        )


class BacktestPreflightTests(unittest.TestCase):
    @patch("src.backend.replay_run_service.backtest_runtime_root")
    @patch("src.backend.replay_run_service.historical_preflight")
    def test_preflight_pins_configuration_accounts_and_external_storage(
        self,
        historical,
        runtime_root,
    ) -> None:
        historical.return_value = {
            "mode": "backtest",
            "window": {
                "sessions": ["2026-07-06", "2026-07-07"],
                "session_count": 2,
                "start": "2026-07-06T04:00:00-04:00",
                "end": "2026-07-07T20:00:00-04:00",
            },
            "checks": [],
            "strategy_run_ready": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            runtime_root.return_value = Path(directory)
            approved = approved_configuration(assignments=[{
                "assignment_id": "assignment-1",
                "account_key": "primary",
                "ticker": "AAPL",
                "conid": 265598,
                "status": "watching",
            }])

            payload = backtest_preflight(
                anchor_date=date(2026, 7, 8),
                session_count=2,
                configuration_revision=approved,
            )

        self.assertTrue(payload["strategy_run_ready"])
        self.assertEqual(payload["configuration_revision_id"], "configuration-test")
        checks = {row["id"]: row for row in payload["checks"]}
        self.assertEqual(checks["simulated_accounts"]["status"], "ready")
        self.assertEqual(checks["runtime_storage"]["status"], "ready")
        self.assertIn(
            "every configured intraday refresh clock",
            checks["strategy_assignments"]["evidence"],
        )

    def test_backtest_definition_spans_sessions_with_one_runtime_window(self) -> None:
        definition = ReplayRunDefinition(
            session_date=date(2026, 7, 6),
            final_session_date=date(2026, 7, 10),
            start_time=time(4, 0),
            configuration_revision=approved_configuration(),
            mode=RunMode.BACKTEST,
        )

        self.assertEqual(definition.session_start.isoformat(), "2026-07-06T04:00:00-04:00")
        self.assertEqual(definition.session_end.isoformat(), "2026-07-10T20:00:00-04:00")
        self.assertEqual(definition.payload()["mode"], "backtest")

    def test_replay_definition_rejects_multi_session_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "limited to one exchange session"):
            ReplayRunDefinition(
                session_date=date(2026, 7, 6),
                final_session_date=date(2026, 7, 10),
                start_time=time(9, 45),
                configuration_revision=approved_configuration(),
            )


class ReplayControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_watchlist_projection_tickers_follow_strategy_entry_session(self) -> None:
        configuration = approved_configuration()
        configuration["payload"]["signal_activation"] = {
            "signal_streams": [{
                "signal_stream_id": "early",
                "occurrence_source": "qmd_squeeze_episode",
                "enabled": True,
            }]
        }
        configuration["payload"]["strategy_profile"] = {
            "lifecycle": {
                "trading_behavior": {
                    "eligible_sessions": ["premarket"],
                    "entry_cutoff_time": "09:29:59",
                }
            }
        }
        controller = ReplayRunController(
            ReplayRunDefinition(
                session_date=date(2026, 8, 21),
                start_time=time(4, 0),
                configuration_revision=configuration,
            ),
            runtime_root=Path(tempfile.gettempdir()),
        )
        controller._historical_external_signal_events = [
            ReplaySignalEvent(
                available_at=available_at,
                occurrence={"event_id": ticker},
                source_values={},
                ticker=ticker,
            )
            for ticker, available_at in (
                ("PRE", datetime(2026, 8, 21, 9, 15, tzinfo=NEW_YORK)),
                ("RTH", datetime(2026, 8, 21, 10, 0, tzinfo=NEW_YORK)),
            )
        ]

        self.assertEqual(
            controller._historical_watchlist_projection_tickers(), ["PRE"]
        )

    async def test_independent_signal_authorities_load_concurrently_and_merge_causally(self) -> None:
        controller = ReplayRunController(
            ReplayRunDefinition(
                session_date=date(2026, 8, 21),
                start_time=time(4, 0),
                configuration_revision=approved_configuration(),
            ),
            runtime_root=Path(tempfile.gettempdir()),
        )
        active = 0
        peak = 0

        async def loaded(ticker: str, second: int) -> list[ReplaySignalEvent]:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            available_at = datetime(2026, 8, 21, 8, 0, second, tzinfo=NEW_YORK)
            return [ReplaySignalEvent(
                available_at=available_at,
                occurrence={"event_id": ticker, "signal_stream_id": "stream"},
                source_values={},
                ticker=ticker,
            )]

        async def market() -> list[ReplaySignalEvent]:
            return await loaded("CCC", 3)

        async def source_native() -> list[ReplaySignalEvent]:
            return await loaded("AAA", 1)

        async def external() -> list[ReplaySignalEvent]:
            return await loaded("BBB", 2)

        with (
            patch.object(controller, "_load_market_signal_events", side_effect=market),
            patch.object(controller, "_load_source_native_signal_events", side_effect=source_native),
            patch.object(controller, "_load_external_signal_events", side_effect=external),
        ):
            events = await controller._load_historical_signal_events()

        self.assertEqual(peak, 3)
        self.assertEqual([event.ticker for event in events], ["AAA", "BBB", "CCC"])

    async def test_source_native_stream_queries_use_bounded_concurrency(self) -> None:
        configuration = approved_configuration()
        configuration["payload"]["signal_activation"] = {
            "signal_streams": [
                {
                    "signal_stream_id": stream_id,
                    "occurrence_source": "qmd_squeeze_episode",
                    "enabled": True,
                }
                for stream_id in ("early", "exact")
            ]
        }
        active = 0
        peak = 0
        lock = threading.Lock()

        def load(stream: dict, **_kwargs: object) -> dict:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            wall_time.sleep(0.03)
            with lock:
                active -= 1
            return {
                "authority": {"signal_stream_id": stream["signal_stream_id"]},
                "occurrences": [],
            }

        with tempfile.TemporaryDirectory() as directory:
            controller = ReplayRunController(
                ReplayRunDefinition(
                    session_date=date(2026, 8, 21),
                    start_time=time(4, 0),
                    configuration_revision=configuration,
                ),
                runtime_root=Path(directory),
            )
            controller._journal = TradingJournal(Path(directory) / "journal.sqlite3")
            with patch(
                "src.backend.historical_signal_occurrence_service."
                "historical_source_native_signal_occurrences",
                side_effect=load,
            ):
                events = await controller._load_source_native_signal_events()
            controller._journal.close()

        self.assertEqual(events, [])
        self.assertEqual(peak, 2)

    async def test_next_action_can_be_queued_before_runtime_warmup_finishes(self) -> None:
        configuration = approved_configuration()
        controller = ReplayRunController(
            ReplayRunDefinition(
                session_date=date(2026, 8, 21),
                start_time=time(4, 0),
                configuration_revision=configuration,
            ),
            runtime_root=Path(tempfile.gettempdir()),
        )
        configuration["payload"]["signal_activation"] = {
            "signal_streams": [{
                "enabled": True,
                "signal_stream_id": "price-squeeze-5m",
            }],
        }
        configuration["payload"]["run_plan"] = {
            "watchlist_ids": ["squeeze-tradable-candidates"],
        }

        result = await controller.command("next_action")

        self.assertEqual(result["status"], "fast_forwarding")
        self.assertEqual(result["transport_mode"], "next_action")
        self.assertFalse(result["runtime_ready"])
        self.assertEqual(result["preparation_stage"], "created")
        self.assertTrue(result["navigation_search"]["active"])
        self.assertEqual(result["navigation_search"]["phase"], "preparing")
        self.assertEqual(result["navigation_search"]["scanned_events"], 0)
        self.assertEqual(
            result["navigation_search"]["start_event_time"],
            "2026-08-21T04:00:00-04:00",
        )
        with patch.object(controller, "_runtime_inputs_ready", True):
            self.assertEqual(
                controller.snapshot()["navigation_search"]["phase"],
                "scanning",
            )
        self.assertEqual(result["execution_mode"], "strategy")
        self.assertEqual(
            result["strategy_debug_sources"]["signal_stream_ids"],
            ["price-squeeze-5m"],
        )
        self.assertEqual(
            result["strategy_debug_sources"]["watchlist_ids"],
            ["squeeze-tradable-candidates"],
        )
        self.assertEqual(controller._next_action_after_sequence, 0)

    async def test_historical_watchlist_warmup_runs_off_event_loop(self) -> None:
        controller = ReplayRunController(
            ReplayRunDefinition(
                session_date=date(2026, 8, 21),
                start_time=time(4, 0),
                configuration_revision=approved_configuration(),
            ),
            runtime_root=Path(tempfile.gettempdir()),
        )
        with patch.object(
            controller,
            "_historical_watchlist_timeline",
            return_value=[],
        ) as materialize, patch(
            "src.backend.replay_run_service.asyncio.to_thread",
            new=AsyncMock(),
        ) as to_thread:
            await controller._prepare_historical_watchlist_timeline()

        to_thread.assert_awaited_once_with(materialize)

    def test_replay_projects_selected_watchlist_as_resolved_when_membership_is_empty(self) -> None:
        configuration = approved_configuration()
        controller = ReplayRunController(
            ReplayRunDefinition(
                session_date=date(2026, 8, 21),
                start_time=time(4, 0),
                configuration_revision=configuration,
            ),
            runtime_root=Path(tempfile.gettempdir()),
        )
        controller._runtime_inputs_ready = True
        controller._strategy_debug_sources = MagicMock(return_value={
            "signal_stream_ids": [],
            "watchlist_ids": ["squeeze-tradable-candidates"],
        })

        runtime = controller.snapshot()["watchlist_runtime"]

        self.assertEqual(runtime["status"], "ready")
        self.assertEqual(runtime["watchlists"], [{
            "watchlist_id": "squeeze-tradable-candidates",
            "status": "ready",
            "members": [],
        }])

    def test_source_native_market_signal_creates_assignment_without_symbol_seed(self) -> None:
        approved = approved_configuration()
        approved["payload"]["run_plan"] = {
            "activation": {"watchlist_policy": "not_required"},
            "watchlist_ids": [],
        }
        controller = ReplayRunController(
            ReplayRunDefinition(
                session_date=date(2026, 8, 21),
                start_time=time(4, 0),
                tickers=(),
                configuration_revision=approved,
            ),
            runtime_root=Path(tempfile.gettempdir()),
        )
        occurrence_time = datetime(2026, 8, 21, 7, 32, tzinfo=NEW_YORK)
        controller._historical_external_signal_events = [
            ReplaySignalEvent(
                available_at=occurrence_time,
                occurrence={
                    "conid": 123456,
                    "event_id": "early-juns-1",
                    "signal_stream_id": "price-squeeze-early",
                    "ticker": "JUNS",
                },
                source_values={},
                ticker="JUNS",
            )
        ]

        assignments = controller._selected_assignments()

        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["ticker"], "JUNS")
        self.assertEqual(assignments[0]["source"], "historical_signal_stream")

    async def test_warmup_market_event_updates_runtime_without_strategy_evaluation(self) -> None:
        controller = ReplayRunController(
            ReplayRunDefinition(
                session_date=date(2026, 8, 21),
                start_time=time(7, 30),
                end_time=time(9, 30),
                configuration_revision=approved_configuration(),
                mode=RunMode.BACKTEST,
            ),
            runtime_root=Path(tempfile.gettempdir()),
        )
        controller._runtime = AsyncMock()
        event = _debug_market_events(({
            "kind": "trade",
            "ticker": "AAPL",
            "ts": "2026-08-21T06:00:00-04:00",
            "price": 10.0,
            "size": 100,
        },))[0]

        await controller._process_market_event(event, evaluate_strategy=False)

        controller._runtime.process_event.assert_awaited_once_with(
            event, evaluate_strategy=False
        )

    async def test_source_native_squeeze_loader_preserves_available_clock_and_authority(self) -> None:
        configuration = approved_configuration()
        configuration["payload"]["signal_activation"] = {
            "signal_streams": [{
                "signal_stream_id": "price-squeeze-5m",
                "occurrence_source": "qmd_squeeze_episode",
                "enabled": True,
            }]
        }
        available_at = datetime(2026, 7, 28, 10, 0, 0, 100000, tzinfo=NEW_YORK)
        loaded = {
            "occurrences": [{
                "event_id": "squeeze-1",
                "signal_stream_id": "price-squeeze-5m",
                "ticker": "AAPL",
                "event_time": available_at.isoformat(),
                "effective_at": available_at.isoformat(),
                "available_at": available_at.isoformat(),
                "squeeze_move_pct": 5.2,
            }],
            "authority": {
                "authority": "qmd_persisted_signal_stream_occurrences",
                "row_count": 1,
                "content_hash": "abc123",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            controller = ReplayRunController(
                ReplayRunDefinition(
                    session_date=date(2026, 7, 28),
                    start_time=time(9, 45),
                    configuration_revision=configuration,
                ),
                runtime_root=Path(directory),
            )
            controller._journal = TradingJournal(Path(directory) / "journal.sqlite3")
            with patch(
                "src.backend.historical_signal_occurrence_service."
                "historical_source_native_signal_occurrences",
                return_value=loaded,
            ):
                events = await controller._load_source_native_signal_events()
            stream_snapshot = controller.signal_stream_snapshot(
                signal_stream_id="price-squeeze-5m",
                as_of=available_at,
            )
            controller._journal.close()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].available_at, available_at)
        self.assertEqual(events[0].ticker, "AAPL")
        self.assertEqual(events[0].occurrence["event_id"], "squeeze-1")
        self.assertEqual(stream_snapshot["occurrence_count"], 1)
        self.assertEqual(stream_snapshot["occurrences"][0]["event_id"], "squeeze-1")
        self.assertEqual(
            controller._data_authority["source_native_signal_stream:price-squeeze-5m"]
            ["content_hash"],
            "abc123",
        )

    async def test_unsupported_native_source_fails_closed(self) -> None:
        configuration = approved_configuration()
        configuration["payload"]["signal_activation"] = {
            "signal_streams": [{
                "signal_stream_id": "unsupported-stream",
                "occurrence_source": "unversioned_native_source",
                "enabled": True,
            }]
        }
        with tempfile.TemporaryDirectory() as directory:
            controller = ReplayRunController(
                ReplayRunDefinition(
                    session_date=date(2026, 7, 28),
                    start_time=time(9, 45),
                    configuration_revision=configuration,
                ),
                runtime_root=Path(directory),
            )
            controller._journal = TradingJournal(Path(directory) / "journal.sqlite3")
            with self.assertRaisesRegex(RuntimeError, "lack an immutable occurrence loader"):
                await controller._load_source_native_signal_events()
            controller._journal.close()

        self.assertEqual(controller._historical_core_signal_plans, [])

    async def test_chart_proposal_captures_snapshot_before_runtime_authority(self) -> None:
        controller = ReplayRunController(
            ReplayRunDefinition(
                session_date=date(2026, 7, 28),
                start_time=time(9, 45),
                tickers=("AAPL",),
                configuration_revision=approved_configuration(),
            ),
            runtime_root=Path(tempfile.gettempdir()),
        )
        controller.current_time = datetime(2026, 7, 28, 10, 0, tzinfo=NEW_YORK)
        controller._runtime = AsyncMock()
        controller._runtime.submit_external_intent.return_value = {
            "proposal_id": "proposal-1",
            "decision": {"status": "approved"},
            "order_group": {"state": "submitted"},
        }
        controller._planner = MagicMock()

        result = await controller.submit_trade_proposal({
            "proposal_id": "proposal-1",
            "authority": "manual",
            "account_id": "SIM-REPLAY",
            "ticker": "AAPL",
            "conid": 265598,
            "action": "enter_long",
            "quantity": 10,
            "market_snapshot": {
                "observed_at": "2026-07-28T09:59:00-04:00",
                "reference_price": 101.25,
                "bid": 101.24,
                "ask": 101.26,
                "tick_size": 0.01,
                "freshness": "ready",
                "source_sequence": "bar-42",
            },
            "invalidation_price": 99.0,
        })

        self.assertEqual(result["proposal"]["market_snapshot"]["source_sequence"], "bar-42")
        intent = controller._runtime.submit_external_intent.await_args.args[0]
        self.assertEqual(intent.reference_price, 101.25)
        self.assertEqual(intent.invalidation_price, 99.0)
        self.assertEqual(intent.metadata["proposal_id"], "proposal-1")
        controller._planner.upsert_instrument.assert_called_once()

    async def test_commands_keep_event_clock_and_transport_state_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = ReplayRunController(
                ReplayRunDefinition(
                    session_date=date(2026, 7, 28),
                    start_time=time(9, 45),
                    tickers=("AAPL",),
                    configuration_revision=approved_configuration(),
                ),
                runtime_root=Path(directory),
            )
            controller.status = "ready"
            controller.current_time = datetime(2026, 7, 28, 9, 45, tzinfo=NEW_YORK)

            played = await controller.command("play")
            self.assertEqual(played["status"], "running")
            self.assertEqual(played["transport_mode"], "play")
            self.assertEqual(played["current_time"], "2026-07-28T09:45:00-04:00")
            self.assertEqual(played["canvas_profile"]["defaultState"]["openIds"], ["chart"])

            stepped = await controller.command("step", step_seconds=5)
            self.assertEqual(stepped["status"], "running")
            self.assertEqual(stepped["transport_mode"], "step")
            self.assertEqual(
                controller._step_until,
                datetime(2026, 7, 28, 9, 45, 5, tzinfo=NEW_YORK),
            )

            await controller.command("set_speed", speed=120)
            self.assertEqual(controller.speed, 120)
            self.assertTrue(controller._pace_reset)

            paused = await controller.command("pause")
            self.assertEqual(paused["status"], "paused")
            self.assertEqual(paused["transport_mode"], "paused")

            jumped = await controller.command("fast_forward", target_time=time(9, 50))
            self.assertEqual(jumped["status"], "fast_forwarding")
            self.assertEqual(jumped["transport_mode"], "fast_forward")

    async def test_step_boundary_forces_paused_state_to_subscribers(self) -> None:
        controller = ReplayRunController(
            ReplayRunDefinition(
                session_date=date(2026, 7, 28),
                start_time=time(9, 45),
                tickers=("AAPL",),
                configuration_revision=approved_configuration(),
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
                configuration_revision=approved_configuration(),
            ),
            runtime_root=Path(tempfile.gettempdir()),
        )
        controller.status = "ready"
        controller.current_time = datetime(2026, 7, 28, 19, 58, tzinfo=NEW_YORK)

        with self.assertRaisesRegex(ValueError, "cannot exceed 20:00"):
            await controller.command("fast_forward", target_time=time(20, 0, 1))

    async def test_next_action_stops_on_causal_strategy_milestone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = ReplayRunController(
                ReplayRunDefinition(
                    session_date=date(2026, 7, 28),
                    start_time=time(9, 45),
                    tickers=("AAPL",),
                    configuration_revision=approved_configuration(),
                ),
                runtime_root=Path(directory),
            )
            controller.status = "ready"
            controller.current_time = datetime(2026, 7, 28, 9, 45, tzinfo=NEW_YORK)
            controller._journal = TradingJournal(Path(directory) / "journal.sqlite")

            result = await controller.command("next_action")
            self.assertEqual(result["status"], "fast_forwarding")
            self.assertEqual(result["transport_mode"], "next_action")
            self.assertTrue(result["navigation_search"]["active"])
            event_time = datetime(2026, 7, 28, 9, 45, 1, tzinfo=NEW_YORK)
            controller._journal.append(
                run_id=controller.run_id,
                category="strategy_decision",
                entity_type="signal",
                entity_id="entry-1",
                event_time=event_time,
                payload={"action": "enter_long", "ticker": "AAPL"},
            )

            await controller._after_event(event_time)

            self.assertEqual(controller.status, "paused")
            self.assertEqual(controller.snapshot()["navigation_action"]["label"], "enter long")
            self.assertEqual(controller.snapshot()["navigation_action"]["ticker"], "AAPL")
            self.assertFalse(controller.snapshot()["navigation_search"]["active"])
            controller._journal.close()

    async def test_next_action_stops_on_preloaded_future_market_signal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = ReplayRunController(
                ReplayRunDefinition(
                    session_date=date(2026, 7, 28),
                    start_time=time(4, 0),
                    tickers=("SUGP",),
                    configuration_revision=approved_configuration(),
                ),
                runtime_root=Path(directory),
            )
            controller.status = "ready"
            controller.current_time = datetime(2026, 7, 28, 4, 0, tzinfo=NEW_YORK)
            controller._journal = TradingJournal(Path(directory) / "journal.sqlite")
            signal_time = datetime(2026, 7, 28, 7, 32, 3, tzinfo=NEW_YORK)
            controller._journal.append(
                run_id=controller.run_id,
                category="market_discovery_signal",
                entity_type="signal_occurrence",
                entity_id="squeeze-1",
                event_time=signal_time,
                payload={
                    "signal_stream_id": "price-squeeze-5m",
                    "signal_stream_name": "Small-cap 5% squeeze",
                    "ticker": "SUGP",
                },
            )

            result = await controller.command("next_action")

            self.assertEqual(
                result["navigation_search"]["target_event_time"],
                signal_time.astimezone(UTC).isoformat(),
            )
            await controller._after_event(signal_time)
            snapshot = controller.snapshot()
            self.assertEqual(snapshot["status"], "paused")
            self.assertEqual(snapshot["navigation_action"]["kind"], "watch_started")
            self.assertEqual(snapshot["navigation_action"]["ticker"], "SUGP")
            self.assertFalse(snapshot["navigation_search"]["active"])
            controller._journal.close()


class ReplayHistoricalSourceTests(unittest.IsolatedAsyncioTestCase):
    def test_batched_qmd_frames_preserve_every_frame_and_metadata(self) -> None:
        frames: list[ReplayDerivedFrame] = []
        metadata = _append_historical_derived_message(
            json.dumps({
                "type": "frames_batch",
                "frames": [
                    {
                        "as_of": "2026-07-28T13:45:00+00:00",
                        "sequence": 1,
                        "bar": {"close": 101.0},
                        "indicator": {"vwap": 100.5},
                    },
                    {
                        "as_of": "2026-07-28T13:45:01+00:00",
                        "sequence": 2,
                        "bar": {"close": 101.5},
                        "indicator": {"vwap": 100.7},
                    },
                ],
            }),
            frames=frames,
            metadata={},
            ticker="AAPL",
            timeframe="1s",
        )
        metadata = _append_historical_derived_message(
            json.dumps({"type": "metadata", "emitted_updates": 2}),
            frames=frames,
            metadata=metadata,
            ticker="AAPL",
            timeframe="1s",
        )

        self.assertEqual([frame.sequence for frame in frames], [1, 2])
        self.assertEqual([frame.bar["close"] for frame in frames], [101.0, 101.5])
        self.assertEqual(metadata["emitted_updates"], 2)

    def test_qmd_payload_authority_normalizes_derived_and_scanner_evidence(self) -> None:
        payload = {
            "cache": {
                "engine_version": "qmd-derived-v28",
                "event_count": 42,
                "source_revision": {
                    "token": "revision-7",
                    "source_plan_hash": "plan-7",
                    "complete_for_history": True,
                    "source_tiers": ["archive"],
                },
            }
        }
        authority = _qmd_payload_authority(payload, authority="qmd_history_derived")
        self.assertEqual(authority["revision_token"], "revision-7")
        self.assertEqual(authority["source_plan_hash"], "plan-7")
        self.assertEqual(authority["engine_version"], "qmd-derived-v28")
        self.assertEqual(authority["event_count"], 42)
        self.assertTrue(authority["complete_for_history"])

    def test_controller_rejects_same_source_key_revision_drift(self) -> None:
        definition = ReplayRunDefinition(
            session_date=date(2026, 7, 28),
            start_time=time(9, 45),
            configuration_revision=approved_configuration(),
        )
        controller = ReplayRunController(
            definition,
            runtime_root=Path(tempfile.gettempdir()),
        )
        first = {"revision_token": "revision-1", "source_plan_hash": "plan-1"}
        controller._record_data_authority("market_events", first)
        controller._record_data_authority("market_events", first)
        with self.assertRaisesRegex(RuntimeError, "data authority changed"):
            controller._record_data_authority(
                "market_events",
                {"revision_token": "revision-2", "source_plan_hash": "plan-1"},
            )

    def test_qmd_payload_authority_fails_closed_without_revision(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "omitted source revision"):
            _qmd_payload_authority({}, authority="qmd_history_scanner")

    def test_signal_lifecycles_are_attached_point_in_time(self) -> None:
        start = datetime(2026, 7, 28, 13, 45, tzinfo=ZoneInfo("UTC"))
        frames = [
            ReplayDerivedFrame(start, {}, {}, 1, "AAPL", "1s"),
            ReplayDerivedFrame(start.replace(second=1), {}, {}, 2, "AAPL", "1s"),
            ReplayDerivedFrame(start.replace(second=2), {}, {}, 3, "AAPL", "1s"),
        ]
        events = [
            {
                "effective_at": start.isoformat(),
                "score": 0.8,
                "signal_key": "price_volume_expansion",
                "state": "triggered",
                "working_timeframe": "1s",
            },
            {
                "effective_at": start.replace(second=2).isoformat(),
                "score": 0.8,
                "signal_key": "price_volume_expansion",
                "state": "resolved",
                "working_timeframe": "1s",
            },
        ]

        _attach_historical_signals(frames, events)

        self.assertEqual(frames[0].signals["price_volume_expansion@1s"], 0.8)
        self.assertEqual(frames[1].signals["price_volume_expansion@1s"], 0.8)
        self.assertNotIn("price_volume_expansion@1s", frames[2].signals)

    async def test_paged_pull_releases_transport_between_replay_batches(self) -> None:
        source = QmdHistoricalEventSource(
            "http://127.0.0.1:8801",
            start=datetime(2026, 7, 28, 9, 45, tzinfo=NEW_YORK),
            end=datetime(2026, 7, 28, 9, 46, tzinfo=NEW_YORK),
            tickers=["AAPL"],
            batch_size=1,
        )
        event = {
            "ask_exchange": 11,
            "ask_price": 100.1,
            "ask_size": 100,
            "bid_exchange": 12,
            "bid_price": 100.0,
            "bid_size": 100,
            "conditions": [],
            "indicators": [],
            "ingest_ts": "2026-07-28T13:45:00+00:00",
            "kind": "quote",
            "raw": {},
            "sequence": 1,
            "tape": 3,
            "ticker": "AAPL",
            "ts": "2026-07-28T13:45:00+00:00",
        }
        cursor = {"ordinal": 1, "sip_timestamp_us": 1_774_708_700_000_000, "ticker": "AAPL"}
        revision = {
            "complete_for_history": True,
            "request_complete": True,
            "source_plan_hash": "fnv1a64:test-plan",
            "source_tiers": ["archive"],
            "token": "revision-7",
        }
        with patch.object(
            source,
            "_read_page",
            side_effect=[
                {
                    "complete": False,
                    "events": [event],
                    "next_cursor": cursor,
                    "source_revision": revision,
                },
                {
                    "complete": True,
                    "events": [{**event, "sequence": 2, "ts": "2026-07-28T13:45:01+00:00"}],
                    "next_cursor": None,
                    "source_revision": revision,
                },
            ],
        ) as read_page:
            batches = [batch async for batch in source.stream()]

        self.assertEqual([batch.events[0].sequence for batch in batches], [1, 2])
        pinned = {
            "complete_for_history": True,
            "request_complete": True,
            "source_plan_hash": "fnv1a64:test-plan",
            "source_tiers": ["archive"],
            "revision_token": "revision-7",
        }
        self.assertEqual(source.source_revision, pinned)
        self.assertEqual(read_page.call_args_list[0].args, (None, None))
        self.assertEqual(read_page.call_args_list[1].args, (cursor, pinned))

    async def test_paged_pull_rejects_changed_source_revision(self) -> None:
        source = QmdHistoricalEventSource(
            "http://127.0.0.1:8801",
            start=datetime(2026, 7, 28, 9, 45, tzinfo=NEW_YORK),
            end=datetime(2026, 7, 28, 9, 46, tzinfo=NEW_YORK),
            tickers=["AAPL"],
            batch_size=1,
        )
        event = {
            "ask_exchange": 11,
            "ask_price": 100.1,
            "ask_size": 100,
            "bid_exchange": 12,
            "bid_price": 100.0,
            "bid_size": 100,
            "conditions": [],
            "indicators": [],
            "ingest_ts": "2026-07-28T13:45:00+00:00",
            "kind": "quote",
            "raw": {},
            "sequence": 1,
            "tape": 3,
            "ticker": "AAPL",
            "ts": "2026-07-28T13:45:00+00:00",
        }
        cursor = {"ordinal": 1, "sip_timestamp_us": 1_774_708_700_000_000, "ticker": "AAPL"}
        with patch.object(
            source,
            "_read_page",
            side_effect=[
                {
                    "complete": False,
                    "events": [event],
                    "next_cursor": cursor,
                    "source_revision": {
                        "complete_for_history": True,
                        "request_complete": True,
                        "source_plan_hash": "plan-a",
                        "source_tiers": ["archive"],
                        "token": "revision-a",
                    },
                },
                {
                    "complete": True,
                    "events": [],
                    "next_cursor": None,
                    "source_revision": {
                        "complete_for_history": True,
                        "request_complete": True,
                        "source_plan_hash": "plan-b",
                        "source_tiers": ["archive"],
                        "token": "revision-b",
                    },
                },
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "source plan changed"):
                _ = [batch async for batch in source.stream()]

    async def test_advancing_paged_pull_accepts_tail_revision_progress(self) -> None:
        source = QmdHistoricalEventSource(
            "http://127.0.0.1:8801",
            start=datetime(2026, 7, 28, 9, 45, tzinfo=NEW_YORK),
            end=datetime(2026, 7, 28, 9, 46, tzinfo=NEW_YORK),
            tickers=["AAPL"],
            batch_size=1,
            revision_policy="advancing",
        )
        event = {
            "ask_exchange": 11,
            "ask_price": 100.1,
            "ask_size": 100,
            "bid_exchange": 12,
            "bid_price": 100.0,
            "bid_size": 100,
            "conditions": [],
            "indicators": [],
            "ingest_ts": "2026-07-28T13:45:00+00:00",
            "kind": "quote",
            "raw": {},
            "sequence": 1,
            "tape": 3,
            "ticker": "AAPL",
            "ts": "2026-07-28T13:45:00+00:00",
        }
        cursor = {"ordinal": 1, "sip_timestamp_us": 1_774_708_700_000_000, "ticker": "AAPL"}
        with patch.object(
            source,
            "_read_page",
            side_effect=[
                {
                    "complete": False,
                    "events": [event],
                    "next_cursor": cursor,
                    "source_revision": {
                        "complete_for_history": False,
                        "request_complete": True,
                        "source_plan_hash": "plan-a",
                        "source_tiers": ["recent", "currentlive"],
                        "token": "revision-a",
                    },
                },
                {
                    "complete": True,
                    "events": [{**event, "sequence": 2}],
                    "next_cursor": None,
                    "source_revision": {
                        "complete_for_history": False,
                        "request_complete": True,
                        "source_plan_hash": "plan-a",
                        "source_tiers": ["recent", "currentlive"],
                        "token": "revision-b",
                    },
                },
            ],
        ):
            batches = [batch async for batch in source.stream()]

        self.assertEqual([batch.events[0].sequence for batch in batches], [1, 2])
        self.assertEqual(source.source_revision["revision_token"], "revision-b")

    async def test_pinned_pull_rejects_explicit_source_gap_before_events(self) -> None:
        source = QmdHistoricalEventSource(
            "http://127.0.0.1:8801",
            start=datetime(2026, 7, 28, 9, 45, tzinfo=NEW_YORK),
            end=datetime(2026, 7, 28, 9, 46, tzinfo=NEW_YORK),
            tickers=["AAPL"],
        )
        with patch.object(
            source,
            "_read_page",
            return_value={
                "complete": True,
                "events": [],
                "next_cursor": None,
                "source_revision": {
                    "complete_for_history": False,
                    "request_complete": False,
                    "source_plan_hash": "plan-gap",
                    "source_tiers": ["recent", "gap", "recent"],
                    "token": "revision-gap",
                },
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "explicit coverage gap"):
                _ = [batch async for batch in source.stream()]

    async def test_pinned_pull_rejects_live_dependent_source_before_events(self) -> None:
        source = QmdHistoricalEventSource(
            "http://127.0.0.1:8801",
            start=datetime(2026, 7, 28, 9, 45, tzinfo=NEW_YORK),
            end=datetime(2026, 7, 28, 9, 46, tzinfo=NEW_YORK),
            tickers=["AAPL"],
        )
        with patch.object(
            source,
            "_read_page",
            return_value={
                "complete": True,
                "events": [],
                "next_cursor": None,
                "source_revision": {
                    "complete_for_history": False,
                    "request_complete": True,
                    "source_plan_hash": "plan-live",
                    "source_tiers": ["recent", "currentlive"],
                    "token": "revision-live",
                },
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "fully durable source plan"):
                _ = [batch async for batch in source.stream()]

    async def test_pinned_pull_rejects_empty_incomplete_pagination(self) -> None:
        source = QmdHistoricalEventSource(
            "http://127.0.0.1:8801",
            start=datetime(2026, 7, 28, 9, 45, tzinfo=NEW_YORK),
            end=datetime(2026, 7, 28, 9, 46, tzinfo=NEW_YORK),
            tickers=["AAPL"],
        )
        with patch.object(
            source,
            "_read_page",
            return_value={
                "complete": False,
                "events": [],
                "next_cursor": None,
                "source_revision": {
                    "complete_for_history": True,
                    "request_complete": True,
                    "source_plan_hash": "plan-durable",
                    "source_tiers": ["archive"],
                    "token": "revision-durable",
                },
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "before complete=true"):
                _ = [batch async for batch in source.stream()]


class ReplayPreflightTests(unittest.TestCase):
    def test_canvas_symbols_include_only_active_configured_instances(self) -> None:
        profile = {
            "workspaceStates": {
                "main": {
                    "instances": {"chart": "chart", "chart-2": "chart"},
                    "openIds": ["chart", "chart-2"],
                }
            },
            "linkAssignments": {"chart": "A", "closed-chart": "B"},
            "linkContexts": {
                "A": {"symbol": "AAPL"},
                "B": {"symbol": "SHOULD_NOT_STREAM"},
                "C": {"symbol": "UNUSED"},
            },
            "instanceSettings": {
                "chart-2": {"chart": {"symbol": "MSFT"}},
                "closed-chart": {"chart": {"symbol": "CLOSED"}},
            },
        }

        self.assertEqual(_canvas_profile_tickers(profile), {"AAPL", "MSFT"})

    def test_preflight_maps_live_accounts_to_explicit_simulated_boundaries(self) -> None:
        assignment = {
            "account_key": "primary",
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
            "src.backend.replay_run_service.replay_runtime_root",
            return_value=Path(directory),
        ):
            result = replay_preflight(
                session_date=date(2026, 7, 28),
                start_time=time(9, 45),
                initial_cash=100_000,
                tickers=("AAPL",),
                configuration_revision=approved_configuration(assignments=[assignment]),
            )

        self.assertTrue(result["ready"])
        self.assertEqual(result["account_mapping"], {"DU123456": "SIM-01-PRIMARY"})
        self.assertTrue(all(check["status"] == "ready" for check in result["checks"]))

    def test_strategy_preflight_uses_run_plan_market_sources_without_symbol(self) -> None:
        approved = approved_configuration()
        approved["payload"]["run_plan"] = {
            "activation": {"watchlist_policy": "any_selected"},
            "watchlist_ids": ["squeeze-tradable-candidates"],
        }
        approved["payload"]["signal_activation"] = {
            "signal_streams": [{
                "enabled": True,
                "occurrence_source": "qmd_squeeze_episode",
                "signal_stream_id": "price-squeeze-early",
            }],
        }
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.backend.replay_run_service.historical_gateway_snapshot",
            return_value={"base_url": "http://127.0.0.1:8801", "ready": True},
        ), patch(
            "src.backend.replay_run_service.historical_day_coverage",
            return_value={
                "coverage_table": "qmd.coverage",
                "event_count": 10_000,
                "ticker_count": 1_000,
            },
        ), patch(
            "src.backend.replay_run_service.replay_runtime_root",
            return_value=Path(directory),
        ):
            result = replay_preflight(
                session_date=date(2026, 7, 28),
                start_time=time(4, 0),
                initial_cash=10_000,
                configuration_revision=approved,
                execution_mode="strategy",
            )

        universe_check = next(
            row for row in result["checks"] if row["id"] == "configured_symbols"
        )
        self.assertTrue(result["ready"])
        self.assertEqual(result["tickers"], [])
        self.assertEqual(universe_check["label"], "Strategy market universe")
        self.assertEqual(universe_check["status"], "ready")
        self.assertIn("every ticker", universe_check["summary"])

    def test_preflight_defers_watchlist_materialization_to_run_warmup(self) -> None:
        approved = approved_configuration()
        approved["payload"]["universes"] = [{
            "enabled": True,
            "name": "VWAP breakout",
            "scanner_view_id": "vwap-breakout",
            "source": "watchlist",
        }]
        approved["configuration_model"] = {"market_discovery": {}}
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.backend.replay_run_service.historical_gateway_snapshot",
            return_value={"base_url": "http://127.0.0.1:8801", "ready": True},
        ), patch(
            "src.backend.replay_run_service.historical_day_coverage",
            return_value={"coverage_table": "qmd.coverage", "event_count": 10, "ticker_count": 1},
        ), patch(
            "src.backend.replay_run_service.replay_runtime_root",
            return_value=Path(directory),
        ), patch(
            "src.backend.watchlist_runtime_service.resolve_historical_watchlist",
            side_effect=AssertionError("preflight must not materialize historical Watchlists"),
        ) as resolver:
            result = replay_preflight(
                session_date=date(2026, 7, 28),
                start_time=time(9, 45),
                initial_cash=100_000,
                configuration_revision=approved,
            )

        self.assertTrue(result["ready"])
        self.assertEqual(result["tickers"], [])
        resolver.assert_not_called()
        check = next(row for row in result["checks"] if row["id"] == "historical_watchlists")
        self.assertIn("materializes asynchronously", check["summary"])

    def test_preflight_does_not_query_partial_historical_watchlist(self) -> None:
        approved = approved_configuration()
        approved["payload"]["universes"] = [{
            "enabled": True,
            "name": "Core candidates",
            "scanner_view_id": "core-candidates",
            "source": "watchlist",
        }]
        approved["configuration_model"] = {"market_discovery": {}}
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.backend.replay_run_service.historical_gateway_snapshot",
            return_value={"base_url": "http://127.0.0.1:8801", "ready": True},
        ), patch(
            "src.backend.replay_run_service.historical_day_coverage",
            return_value={"coverage_table": "qmd.coverage", "event_count": 10, "ticker_count": 1},
        ), patch(
            "src.backend.replay_run_service.replay_runtime_root",
            return_value=Path(directory),
        ), patch(
            "src.backend.watchlist_runtime_service.resolve_historical_watchlist",
            side_effect=AssertionError("preflight must not inspect historical membership"),
        ) as resolver:
            result = replay_preflight(
                session_date=date(2026, 7, 28),
                start_time=time(9, 45),
                initial_cash=100_000,
                configuration_revision=approved,
            )

        self.assertTrue(result["ready"])
        resolver.assert_not_called()
        check = next(row for row in result["checks"] if row["id"] == "historical_watchlists")
        self.assertEqual(check["status"], "ready")
        self.assertIn("materializes asynchronously", check["summary"])


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
            run_id="replay-run",
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

    def test_runtime_planner_keeps_strategy_lineage_out_of_cpapi_payload(self) -> None:
        planner = RuntimeIbkrStrategyOrderPlanner(
            {
                "AAPL": InstrumentContract(
                    instrument_id="simulated:265598",
                    conid=265598,
                    symbol="AAPL",
                    security_type="STK",
                    currency="USD",
                    exchange="SMART",
                )
            },
            strategy_id=STRATEGY_ID,
            strategy_revision=STRATEGY_REVISION,
            run_id="replay-run",
        )
        intent = StrategyIntent(
            intent_id="intent-1",
            ticker="AAPL",
            action="reduce_long",
            quantity=1.0,
            reference_price=100.0,
            reason="test",
            event_time=datetime(2026, 7, 28, 9, 45, tzinfo=NEW_YORK),
            metadata={"assignment_id": "assignment-1"},
        )

        order = planner.plan(intent=intent, account_id="SIM-REPLAY", event=None).orders[0]

        self.assertEqual(order.raw["canonical_run_id"], "replay-run")
        self.assertNotIn("strategy", order.to_cpapi())
        self.assertFalse(any(key.startswith("canonical_") for key in order.to_cpapi()))

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
