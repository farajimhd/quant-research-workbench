from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from src.backend.trading_configuration_service import (
    _default_draft,
    _resolve_watchlist_universe,
)
from src.backend.watchlist_runtime_service import (
    WatchlistRuntime,
    focused_target_contract,
    live_market_reference_projection,
    normalize_watchlist_candidate,
    publish_watchlist_target,
    publish_computation_target,
    resolve_historical_watchlist,
    strategy_target_contracts,
    watchlist_requires_focused_evidence,
)
from src.backend import watchlist_runtime_service
from src.trading_runtime.journal import TradingJournal


class FakeJournal:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def append(self, **payload):
        self.rows.append(payload)

    def watchlist_membership_records(self, *, limit: int):
        return []


class WatchlistRuntimeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.configuration = _default_draft()
        discovery = self.configuration["market_discovery"]
        discovery["watchlists"] = [
            {
                **next(
                    row
                    for row in discovery["watchlists"]
                    if row["watchlist_id"] == "top-small-cap-gainers"
                ),
                "maximum_size": 2,
                "calculations": [
                    "qmd.family.momentum_core",
                    "qmd.family.trend_moving_averages",
                ],
            }
        ]
        discovery["core_scan"]["calculations"].extend(
            [
                {
                    "capability_id": "qmd.family.momentum_core",
                    "availability": "implemented",
                    "selected_timeframes": ["1m"],
                },
                {
                    "capability_id": "qmd.family.trend_moving_averages",
                    "availability": "implemented",
                    "selected_timeframes": ["1m"],
                },
            ]
        )

    def test_normalizes_core_scanner_contract_for_watchlist_rules(self) -> None:
        row = normalize_watchlist_candidate(
            {
                "ticker": "aapl",
                "last_close": 110,
                "previous_close": 100,
                "last_day_volume_so_far": 50_000,
                "live_priority": 9,
            }
        )
        self.assertEqual(row["ticker"], "AAPL")
        self.assertAlmostEqual(row["change_pct"], 10)
        self.assertEqual(row["volume"], 50_000)
        self.assertEqual(row["liquidity_rank"], 9)

    @patch("src.backend.watchlist_runtime_service.publish_watchlist_target")
    def test_resolves_ranks_journals_and_publishes_exact_membership(self, publish) -> None:
        runtime = WatchlistRuntime()
        journal = FakeJournal()
        candidates = [
            {
                "ticker": "AAA",
                "market_cap": 1_000_000_000,
                "change_pct": 4.0,
                "qmd_structure_timeframe_states": [{"timeframe": "1s"}],
            },
            {"ticker": "BBB", "market_cap": 500_000_000, "change_pct": 9.0},
            {"ticker": "CCC", "market_cap": 900_000_000, "change_pct": 6.0},
        ]
        as_of = datetime(2026, 8, 10, 16, tzinfo=UTC)

        snapshot = runtime.resolve(
            self.configuration,
            candidates,
            as_of=as_of,
            journal=journal,
        )

        watchlist = snapshot["watchlists"][0]
        self.assertEqual([row["ticker"] for row in watchlist["members"]], ["BBB", "CCC"])
        self.assertNotIn("qmd_structure_timeframe_states", watchlist["members"][0])
        self.assertIn("market_cap", watchlist["members"][0])
        self.assertIn("change_pct", watchlist["members"][0])
        self.assertEqual(len(journal.rows), 2)
        self.assertTrue(all(row["category"] == "watchlist_membership" for row in journal.rows))
        publish.assert_called_once()
        self.assertEqual(publish.call_args.args[1], ["BBB", "CCC"])

        second = runtime.resolve(
            self.configuration,
            candidates[:1],
            as_of=as_of,
            journal=journal,
            publish_targets=False,
        )
        self.assertEqual(second["watchlists"][0]["events"][0]["event"], "added")
        self.assertEqual(
            {row["event"] for row in second["watchlists"][0]["events"]},
            {"added"},
        )
        self.assertEqual(second["watchlists"][0]["member_count"], 3)

    @patch("src.backend.watchlist_runtime_service.publish_watchlist_target")
    def test_recomputes_only_candidates_with_rule_relevant_changes(self, publish) -> None:
        runtime = WatchlistRuntime()
        candidates = [
            {
                "ticker": "AAA",
                "market_cap": 1_000_000_000,
                "change_pct": 4.0,
                "reference_available_at": "revision-1",
            },
            {
                "ticker": "BBB",
                "market_cap": 500_000_000,
                "change_pct": 9.0,
                "reference_available_at": "revision-1",
            },
        ]

        first = runtime.resolve(self.configuration, candidates)
        provenance_only = runtime.resolve(
            self.configuration,
            [
                {**row, "reference_available_at": "revision-2"}
                for row in candidates
            ],
        )
        one_changed = runtime.resolve(
            self.configuration,
            [candidates[0], {**candidates[1], "change_pct": 3.0}],
        )

        self.assertEqual(
            first["watchlists"][0]["recomputed_candidate_count"], 2
        )
        self.assertEqual(
            provenance_only["watchlists"][0]["recomputed_candidate_count"], 0
        )
        self.assertEqual(
            one_changed["watchlists"][0]["recomputed_candidate_count"], 1
        )
        self.assertEqual(
            [row["ticker"] for row in one_changed["watchlists"][0]["members"]],
            ["AAA", "BBB"],
        )

    def test_focused_contract_translates_only_runnable_qmd_families(self) -> None:
        discovery = self.configuration["market_discovery"]
        calculations = {
            row["capability_id"]: row
            for row in discovery["core_scan"]["calculations"]
        }
        capabilities, timeframes = focused_target_contract(
            discovery["watchlists"][0], calculations
        )
        self.assertEqual(
            capabilities, ["momentum_core", "trend_moving_averages"]
        )
        self.assertIn("1m", timeframes)

    def test_live_strategy_target_contract_comes_from_compiled_run_plan_dependencies(self) -> None:
        watchlist_id = self.configuration["market_discovery"]["watchlists"][0]["watchlist_id"]
        self.configuration["run_plans"] = {
            "universes": [{
                "universe_id": "live-small-caps",
                "source": "watchlist",
                "scanner_view_id": watchlist_id,
            }],
            "plans": [{
                "run_plan_id": "paper-momentum",
                "universe_id": "live-small-caps",
                "allowed_environments": ["paper"],
                "enabled": True,
                "observation_dependencies": [
                    {"producer": "qmd", "capability_key": "momentum_core", "timeframes": ["5s"]},
                    {"producer": "qmd", "capability_key": "qmd_generic_structure", "timeframes": ["1s"]},
                    {"producer": "news_gateway", "capability_key": "company_news", "timeframes": []},
                ],
            }],
        }

        contracts = strategy_target_contracts(self.configuration, watchlist_id)

        self.assertEqual(contracts, [{
            "run_plan_id": "paper-momentum",
            "capabilities": ["momentum_core", "qmd_generic_structure"],
            "timeframes": ["1s", "5s"],
        }])

    @patch("src.backend.watchlist_runtime_service.publish_computation_target")
    @patch("src.backend.watchlist_runtime_service.publish_watchlist_target")
    def test_exact_watchlist_members_publish_strategy_run_lease(
        self, publish_watchlist, publish_strategy
    ) -> None:
        watchlist_id = self.configuration["market_discovery"]["watchlists"][0]["watchlist_id"]
        self.configuration["run_plans"] = {
            "universes": [{
                "universe_id": "live-small-caps",
                "source": "watchlist",
                "scanner_view_id": watchlist_id,
            }],
            "plans": [{
                "run_plan_id": "paper-momentum",
                "universe_id": "live-small-caps",
                "allowed_environments": ["paper"],
                "enabled": True,
                "observation_dependencies": [{
                    "producer": "qmd",
                    "capability_key": "momentum_core",
                    "timeframes": ["5s"],
                }],
            }],
        }

        WatchlistRuntime().resolve(
            self.configuration,
            [{"ticker": "AAA", "market_cap": 1_000_000_000, "change_pct": 4.0}],
            as_of=datetime(2026, 8, 10, 16, tzinfo=UTC),
        )

        publish_watchlist.assert_called_once()
        publish_strategy.assert_called_once_with(
            "strategy:paper-momentum",
            ["AAA"],
            ["momentum_core"],
            ["5s"],
            owner="backend.strategy_runtime",
            scope="strategy_run",
            ttl_ms=300_000,
            causation_seed=(
                "top-small-cap-gainers:2026-08-10T16:00:00+00:00"
            ),
        )

    @patch("src.backend.watchlist_runtime_service.publish_watchlist_target")
    def test_vwap_rule_seeds_bounded_focused_candidates(self, publish) -> None:
        discovery = self.configuration["market_discovery"]
        vwap = next(
            row for row in _default_draft()["market_discovery"]["watchlists"]
            if row["watchlist_id"] == "vwap-breakout"
        )
        vwap = {
            **vwap,
            "maximum_size": 2,
            "calculations": ["qmd.family.momentum_core"],
        }
        discovery["watchlists"] = [vwap]
        rule_sets = {row["rule_set_id"]: row for row in discovery["rule_sets"]}
        self.assertTrue(watchlist_requires_focused_evidence(vwap, rule_sets))

        seeded = WatchlistRuntime().seed_focused_targets(
            self.configuration,
            [
                {"ticker": f"T{index}", "liquidity_rank": index}
                for index in range(20)
            ],
        )

        self.assertEqual(seeded[0]["candidate_count"], 10)
        self.assertEqual(len(publish.call_args.args[1]), 10)

    @patch("src.backend.watchlist_runtime_service.qmd_delete_json")
    @patch("src.backend.watchlist_runtime_service.qmd_put_json")
    def test_empty_membership_releases_target(self, put_json, delete_json) -> None:
        publish_watchlist_target(
            "small",
            [],
            ["momentum_core"],
            ["1m"],
            ttl_ms=300_000,
        )
        put_json.assert_not_called()
        delete_json.assert_called_once_with(
            "/computation-targets/watchlist:small", timeout=3
        )

    @patch("src.backend.watchlist_runtime_service.qmd_put_json")
    def test_autonomous_target_publishes_explicit_lineage(self, put_json) -> None:
        publish_computation_target(
            "strategy:run-7",
            ["AAPL"],
            ["momentum_core"],
            ["1m"],
            owner="backend.strategy_runtime",
            scope="strategy_run",
            ttl_ms=300_000,
            causation_seed="watchlist:small:revision-19",
        )

        lease = put_json.call_args.args[1]
        self.assertEqual(lease["correlation_id"], "run:strategy:run-7")
        self.assertEqual(
            lease["causation_id"], "event:watchlist:small:revision-19"
        )

    @patch("src.backend.watchlist_runtime_service.ClickHouseHttpClient")
    @patch(
        "src.backend.historical_scanner_service.historical_scanner_reference_projection"
    )
    def test_reference_cache_isolated_by_explicit_as_of(
        self,
        historical_projection,
        clickhouse_client,
    ) -> None:
        historical_projection.side_effect = lambda cutoff: {
            "AAPL": {"reference_available_at": cutoff.isoformat()}
        }
        clickhouse_client.return_value.execute.return_value = ""
        first_clock = datetime(2026, 8, 10, 15, tzinfo=UTC)
        second_clock = datetime(2026, 8, 10, 15, 0, 30, tzinfo=UTC)

        first = live_market_reference_projection(first_clock)
        repeated = live_market_reference_projection(first_clock)
        second = live_market_reference_projection(second_clock)

        self.assertEqual(first, repeated)
        self.assertNotEqual(
            first["AAPL"]["reference_available_at"],
            second["AAPL"]["reference_available_at"],
        )
        self.assertEqual(historical_projection.call_count, 2)

    @patch("src.backend.watchlist_runtime_service.threading.Thread")
    @patch("src.backend.watchlist_runtime_service._load_market_reference_projection")
    def test_live_reference_expiry_returns_stale_and_single_flights_refresh(
        self, load_projection, thread
    ) -> None:
        cutoff = datetime(2026, 8, 10, 16, tzinfo=UTC)
        old = {"OLD": {"reference_available_at": "old"}}
        new = {"NEW": {"reference_available_at": "new"}}
        load_projection.return_value = new
        watchlist_runtime_service._LIVE_REFERENCE_PROJECTION = old
        watchlist_runtime_service._LIVE_REFERENCE_LOADED_AT = cutoff - timedelta(seconds=61)
        watchlist_runtime_service._LIVE_REFERENCE_REFRESHING = False

        class ImmediateThread:
            def __init__(self, *, target, args, **kwargs):
                self.target = target
                self.args = args

            def start(self):
                self.target(*self.args)

        thread.side_effect = ImmediateThread
        try:
            returned = live_market_reference_projection()
            self.assertIs(returned, old)
            self.assertEqual(watchlist_runtime_service._LIVE_REFERENCE_PROJECTION, new)
            self.assertFalse(watchlist_runtime_service._LIVE_REFERENCE_REFRESHING)
            thread.assert_called_once()
        finally:
            watchlist_runtime_service._LIVE_REFERENCE_PROJECTION = None
            watchlist_runtime_service._LIVE_REFERENCE_LOADED_AT = None
            watchlist_runtime_service._LIVE_REFERENCE_REFRESHING = False

    def test_membership_journal_rehydrates_current_projection(self) -> None:
        with TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            journal.append(
                run_id="watchlist:test",
                category="watchlist_membership",
                entity_type="watchlist_member",
                entity_id="test:AAPL",
                event_time=datetime(2026, 8, 10, 16, tzinfo=UTC),
                payload={
                    "event": "added",
                    "watchlist_id": "test",
                    "ticker": "AAPL",
                    "available_at": "2026-08-10T16:00:00+00:00",
                    "reason": "rules passed",
                },
            )
            runtime = WatchlistRuntime()
            empty_configuration = {"market_discovery": {"watchlists": [], "rule_sets": []}}

            runtime.resolve(
                empty_configuration,
                [],
                journal=journal,
                publish_targets=False,
            )

            restored = runtime.snapshot()
            self.assertEqual(restored["status"], "ready")
            self.assertEqual(restored["history_count"], 1)
            self.assertEqual(restored["watchlists"][0]["members"][0]["ticker"], "AAPL")
            self.assertEqual(restored["history"][0]["event"], "added")
            journal.close()

    @patch("src.backend.watchlist_runtime_service.publish_watchlist_target")
    def test_ttl_retains_then_expires_unconfirmed_membership(self, publish) -> None:
        watchlist = self.configuration["market_discovery"]["watchlists"][0]
        watchlist["membership_expiry"] = "time_to_live"
        watchlist["membership_ttl_ms"] = 1_000
        runtime = WatchlistRuntime()
        start = datetime(2026, 8, 10, 16, tzinfo=UTC)
        candidates = [
            {"ticker": "AAA", "market_cap": 1_000_000_000, "change_pct": 4.0}
        ]

        runtime.resolve(self.configuration, candidates, as_of=start)
        retained = runtime.resolve(
            self.configuration,
            [],
            as_of=start + timedelta(milliseconds=500),
        )
        expired = runtime.resolve(
            self.configuration,
            [],
            as_of=start + timedelta(seconds=2),
        )

        self.assertEqual(retained["watchlists"][0]["member_count"], 1)
        self.assertEqual(expired["watchlists"][0]["member_count"], 0)
        self.assertEqual(expired["watchlists"][0]["events"][0]["event"], "expired")

    @patch("src.backend.watchlist_runtime_service.WATCHLIST_RUNTIME")
    def test_run_plan_resolves_live_watchlist_but_not_historical_clock(self, runtime) -> None:
        runtime.snapshot.return_value = {
            "as_of": "2026-08-10T16:00:00+00:00",
            "watchlists": [
                {
                    "watchlist_id": "small",
                    "members": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
                }
            ],
        }
        universe = {"source": "watchlist", "scanner_view_id": "small"}

        live = _resolve_watchlist_universe(universe, mode="paper")
        replay = _resolve_watchlist_universe(universe, mode="replay")

        self.assertEqual(live["symbols"], ["AAPL", "MSFT"])
        self.assertTrue(live["resolved"])
        self.assertEqual(replay["symbols"], [])
        self.assertEqual(replay["resolution_status"], "historical_membership_required")

    @patch(
        "src.backend.historical_scanner_service.historical_scanner_fundamental_projection"
    )
    @patch(
        "src.backend.historical_scanner_service.historical_scanner_technical_projection"
    )
    @patch(
        "src.backend.historical_scanner_service.historical_scanner_reference_projection"
    )
    @patch("src.backend.historical_scanner_service.historical_scanner_snapshot")
    def test_historical_watchlist_uses_as_of_scanner_and_focused_projection(
        self, scanner, reference, technical, fundamentals
    ) -> None:
        discovery = self.configuration["market_discovery"]
        vwap = next(
            row
            for row in _default_draft()["market_discovery"]["watchlists"]
            if row["watchlist_id"] == "vwap-breakout"
        )
        discovery["watchlists"] = [vwap]
        scanner.return_value = (
            [{"ticker": "AAPL", "last": 101.0, "change_pct": 1.0}],
            {
                "complete_universe": True,
                "schema_version": "canvas_historical_scanner_v1",
                "snapshot_at_utc": "2026-08-10T16:00:00+00:00",
                "source_revision": "archive:revision-17",
                "status": "ready",
            },
        )
        reference.return_value = {"AAPL": {"market_cap": 1_000_000_000}}
        technical.return_value = (
            {"AAPL": {"technical__vwap__1s__hlc3": 100.0}},
            {
                "technical_calculation_windows": ["1s"],
                "technical_schema_version": "canvas_scanner_technical_v3",
                "source_revision": "archive:revision-17",
                "technical_windows": {
                    "1s": {
                        "window_start_utc": "2026-08-10T15:59:59+00:00",
                        "window_end_utc": "2026-08-10T16:00:00+00:00",
                    }
                },
            },
        )
        fundamentals.return_value = {}
        as_of = datetime(2026, 8, 10, 16, tzinfo=UTC)

        result = resolve_historical_watchlist(
            self.configuration, "vwap-breakout", as_of=as_of
        )

        self.assertEqual(result["members"][0]["ticker"], "AAPL")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(
            result["authority"]["scanner"]["source_revision"],
            "archive:revision-17",
        )
        self.assertEqual(
            result["authority"]["technical"]["source_revision"],
            "archive:revision-17",
        )
        self.assertEqual(
            result["authority"]["reference"]["query_plan_id"],
            "reference.scanner_asof.v1",
        )
        self.assertIsNone(result["authority"]["fundamentals"])
        technical.assert_called_once_with(as_of, calculation_windows=["1s"])
        fundamentals.assert_not_called()


if __name__ == "__main__":
    unittest.main()
