from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import asdict
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from src.backend.replay_run_service import (
    HistoricalDebugFixture,
    ReplayDerivedFrame,
    ReplayRunController,
    ReplayRunCapacityError,
    ReplayRunDefinition,
    ReplayRunService,
    _attach_historical_signals,
    _canvas_profile_tickers,
    _historical_watchlist_membership_timeline_for_configuration,
    _historical_signal_events,
    _qmd_payload_authority,
    _debug_derived_frames,
    _debug_market_events,
    backtest_debug_preflight,
    backtest_preflight,
    replay_preflight,
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

    def test_parses_canonical_events_and_frames_without_qmd(self) -> None:
        fixture = self.fixture()
        events = _debug_market_events(fixture.market_events)
        frames = _debug_derived_frames(fixture.derived_frames)

        self.assertEqual([event.kind for event in events], ["quote", "trade"])
        self.assertEqual(events[0].source, "debug_fixture:deterministic")
        self.assertEqual(frames[0].indicator["vwap"], 101.1)

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
            controller._write_manifest()
            fixture_payload = json.loads(
                (controller.run_dir / "debug-fixture.json").read_text(encoding="utf-8")
            )
            manifest = json.loads(
                (controller.run_dir / "manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(fixture_payload["fixture_id"], "deterministic-aapl")
        self.assertEqual(fixture_payload["market_event_count"], 2)
        self.assertEqual(fixture_payload["content_hash"], self.fixture().content_hash)
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
                self.assertFalse(checkpoint["resume_supported"])
                qmd_source.assert_not_called()
            finally:
                if controller._journal is not None:
                    controller._journal.close()

    def test_debug_preflight_uses_configuration_and_external_storage_not_qmd(self) -> None:
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
                configuration_revision=approved_configuration(),
            )

        self.assertTrue(payload["ready"])
        self.assertEqual(payload["configuration_revision_id"], "configuration-test")
        historical.assert_not_called()


class HistoricalWatchlistTimelineTests(unittest.TestCase):
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
                    "members": [{"ticker": "AAPL", "ibkr_conid": 1}],
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
            controller._apply_historical_watchlist_membership(
                datetime(2026, 8, 11, 4, 1, tzinfo=NEW_YORK)
            )

            self.assertEqual(controller._active_historical_watchlist_tickers, {"MSFT"})
            events = [
                record.payload["event"]
                for record in controller._journal.watchlist_membership_records()
            ]
            self.assertEqual(events, ["added", "added", "removed"])
            authority = controller.snapshot()["data_authority"]["sources"]
            self.assertTrue(
                any(key.startswith("watchlist_membership:") for key in authority)
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
    async def test_loads_scanner_signals_once_and_bounds_derived_fetches(self) -> None:
        definition = ReplayRunDefinition(
            session_date=date(2026, 8, 10),
            start_time=time(9, 45),
            tickers=tuple(f"T{index}" for index in range(12)),
            configuration_revision=approved_configuration(),
        )
        controller = ReplayRunController(
            definition,
            runtime_root=Path(tempfile.gettempdir()),
        )
        assignments = [
            MagicMock(ticker=f"T{index}", parameters={}) for index in range(12)
        ]
        controller._strategy = MagicMock()
        controller._strategy.assignments.return_value = assignments
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
                "src.backend.replay_run_service.strategy_rule_timeframes",
                return_value={"1m"},
            ),
            patch(
                "src.backend.replay_run_service._historical_derived_frames",
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

        self.assertEqual(frames, [])
        self.assertEqual(derived_fetch.call_count, 12)
        self.assertLessEqual(maximum_active, 8)
        signal_fetch.assert_awaited_once_with(
            tickers=tuple(sorted(f"T{index}" for index in range(12))),
            start=definition.session_start,
            end=definition.session_end,
            authority_sink=controller._record_data_authority,
        )

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


class ReplayHistoricalSourceTests(unittest.IsolatedAsyncioTestCase):
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
            "source_plan_hash": "fnv1a64:test-plan",
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
            "source_plan_hash": "fnv1a64:test-plan",
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
                        "source_plan_hash": "plan-a",
                        "token": "revision-a",
                    },
                },
                {
                    "complete": True,
                    "events": [],
                    "next_cursor": None,
                    "source_revision": {
                        "source_plan_hash": "plan-b",
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
                        "source_plan_hash": "plan-a",
                        "token": "revision-a",
                    },
                },
                {
                    "complete": True,
                    "events": [{**event, "sequence": 2}],
                    "next_cursor": None,
                    "source_revision": {
                        "source_plan_hash": "plan-a",
                        "token": "revision-b",
                    },
                },
            ],
        ):
            batches = [batch async for batch in source.stream()]

        self.assertEqual([batch.events[0].sequence for batch in batches], [1, 2])
        self.assertEqual(source.source_revision["revision_token"], "revision-b")


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

    def test_preflight_resolves_watchlist_at_replay_clock(self) -> None:
        approved = approved_configuration()
        approved["payload"]["universes"] = [{
            "enabled": True,
            "name": "VWAP breakout",
            "scanner_view_id": "vwap-breakout",
            "source": "watchlist",
        }]
        approved["configuration_model"] = {"market_discovery": {}}
        resolved = {
            "members": [{"ticker": "MSFT", "ibkr_conid": 272093}],
            "scanner": {"complete_universe": True},
            "status": "ready",
        }
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
            return_value=resolved,
        ) as resolver:
            result = replay_preflight(
                session_date=date(2026, 7, 28),
                start_time=time(9, 45),
                initial_cash=100_000,
                configuration_revision=approved,
            )

        self.assertTrue(result["ready"])
        self.assertIn("MSFT", result["tickers"])
        self.assertEqual(
            resolver.call_args.kwargs["as_of"].isoformat(),
            "2026-07-28T09:45:00-04:00",
        )

    def test_preflight_fails_closed_for_partial_historical_watchlist(self) -> None:
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
            return_value={
                "members": [{"ticker": "AAPL"}],
                "scanner": {"complete_universe": False},
                "status": "refreshing",
            },
        ):
            result = replay_preflight(
                session_date=date(2026, 7, 28),
                start_time=time(9, 45),
                initial_cash=100_000,
                configuration_revision=approved,
            )

        self.assertFalse(result["ready"])
        check = next(row for row in result["checks"] if row["id"] == "historical_watchlists")
        self.assertEqual(check["status"], "blocked")
        self.assertIn("complete full-universe snapshot", check["evidence"])


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
