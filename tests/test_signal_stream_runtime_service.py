from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from src.backend.discovery_projection import project_discovery_columns
from src.backend.signal_stream_runtime_service import SignalStreamRuntime, signal_stream_session
from src.backend.trading_configuration_service import _default_draft
from src.backend.watchlist_runtime_service import WatchlistRuntime
from src.trading_runtime.journal import TradingJournal


class SignalStreamRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.journal = TradingJournal(Path(self.temporary.name) / "journal.sqlite3")
        self.configuration = _default_draft()
        self.configuration["market_discovery"]["signal_streams"] = [
            {
                "signal_stream_id": "positive-move-signals",
                "revision": 1,
                "name": "Positive move signals",
                "description": "One occurrence per positive transition.",
                "enabled": True,
                "origin": "user",
                "source_type": "core_scan",
                "source_id": "qmd-core-scan",
                "source_scan_id": "qmd-core-scan",
                "inclusion_rule_sets": ["watchlist-positive-gainer"],
                "inclusion_operator": "all",
                "columns": ["symbol", "change_pct", "fundamental_trajectory", "fundamental_quality"],
                "refresh_interval_ms": 1000,
                "trigger_policy": "false_to_true",
                "rearm_policy": "after_false",
                "cooldown_ms": 0,
                "maximum_events": 5000,
                "watchlist_routes": [
                    {
                        "watchlist_id": "top-large-cap-gainers",
                        "membership_expiry": "time_to_live",
                        "membership_ttl_ms": 60_000,
                    }
                ],
            }
        ]

    def tearDown(self) -> None:
        self.journal.close()
        self.temporary.cleanup()

    def test_projection_materializes_registered_alias_columns(self) -> None:
        row = project_discovery_columns(
            [
                {
                    "ticker": "AAA",
                    "fundamental_trajectory": None,
                    "financial_trajectory_score": 81,
                    "xbrl_quality_score": 73,
                    "ipo_date": "2026-08-20",
                    "split_execution_date": "2026-09-01",
                }
            ]
        )[0]
        self.assertEqual(row["symbol"], "AAA")
        self.assertEqual(row["fundamental_trajectory"], 81)
        self.assertEqual(row["fundamental_quality"], 73)
        self.assertEqual(row["ipo_event"], "2026-08-20")
        self.assertEqual(row["split_event"], "2026-09-01")

    def test_occurrences_are_edge_triggered_frozen_and_restart_safe(self) -> None:
        runtime = SignalStreamRuntime()
        start = datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        matching = {
            "ticker": "AAA",
            "change_pct": 4.5,
            "market_cap": 500_000_000,
            "financial_trajectory_score": 81,
            "xbrl_quality_score": 73,
            "company_name": "Alpha Analytics",
            "country": "US",
            "logo_url": "https://example.test/aaa.svg",
            "live_news_recency": "hot",
            "sec_recency": "cold",
        }
        first = runtime.resolve(
            self.configuration, [matching], as_of=start, journal=self.journal
        )
        repeated = runtime.resolve(
            self.configuration,
            [{**matching, "change_pct": 7.0, "financial_trajectory_score": 22, "live_news_recency": "none", "sec_recency": "hot"}],
            as_of=start + timedelta(seconds=1),
            journal=self.journal,
        )
        self.assertEqual(first["signal_streams"][0]["emitted_count"], 1)
        self.assertEqual(repeated["signal_streams"][0]["emitted_count"], 0)
        self.assertEqual(repeated["occurrence_count"], 1)
        self.assertEqual(repeated["occurrences"][0]["change_pct"], 4.5)
        self.assertEqual(repeated["occurrences"][0]["fundamental_trajectory"], 81)
        self.assertEqual(repeated["occurrences"][0]["company_name"], "Alpha Analytics")
        self.assertEqual(repeated["occurrences"][0]["country"], "US")
        self.assertEqual(repeated["occurrences"][0]["live_news_recency"], "hot")
        self.assertEqual(repeated["occurrences"][0]["sec_recency"], "cold")

        restarted = SignalStreamRuntime()
        after_restart = restarted.resolve(
            self.configuration,
            [matching],
            as_of=start + timedelta(seconds=2),
            journal=self.journal,
        )
        self.assertEqual(after_restart["signal_streams"][0]["emitted_count"], 0)
        restarted.resolve(
            self.configuration,
            [{**matching, "change_pct": -1.0}],
            as_of=start + timedelta(seconds=3),
            journal=self.journal,
        )
        rearmed = restarted.resolve(
            self.configuration,
            [{**matching, "change_pct": 2.0}],
            as_of=start + timedelta(seconds=4),
            journal=self.journal,
        )
        self.assertEqual(rearmed["signal_streams"][0]["emitted_count"], 1)

    def test_default_halt_stream_emits_once_and_rearms_after_resume(self) -> None:
        configuration = _default_draft()
        halt_stream = next(
            stream
            for stream in configuration["market_discovery"]["signal_streams"]
            if stream["signal_stream_id"] == "market-halts"
        )
        configuration["market_discovery"]["signal_streams"] = [halt_stream]
        runtime = SignalStreamRuntime()
        start = datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        halted = {"ticker": "HALT", "market_is_halted": True, "last_price": 12.34}

        first = runtime.resolve(configuration, [halted], as_of=start, journal=self.journal)
        repeated = runtime.resolve(
            configuration,
            [halted],
            as_of=start + timedelta(seconds=1),
            journal=self.journal,
        )
        runtime.resolve(
            configuration,
            [{**halted, "market_is_halted": False}],
            as_of=start + timedelta(seconds=2),
            journal=self.journal,
        )
        rearmed = runtime.resolve(
            configuration,
            [halted],
            as_of=start + timedelta(seconds=3),
            journal=self.journal,
        )

        self.assertEqual(first["signal_streams"][0]["emitted_count"], 1)
        self.assertEqual(repeated["signal_streams"][0]["emitted_count"], 0)
        self.assertEqual(first["new_occurrences"][0]["signal_stream_id"], "market-halts")
        self.assertIs(first["new_occurrences"][0]["market_is_halted"], True)
        self.assertEqual(rearmed["signal_streams"][0]["emitted_count"], 1)
        self.assertEqual(rearmed["occurrence_count"], 2)

    def test_signal_route_admits_without_mutating_occurrence(self) -> None:
        runtime = SignalStreamRuntime()
        at = datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        candidate = {"ticker": "AAA", "change_pct": 4.5, "market_cap": 500_000_000}
        signal = runtime.resolve(
            self.configuration, [candidate], as_of=at, journal=self.journal
        )
        admissions = signal["admissions_by_watchlist"]
        self.assertEqual(
            admissions["top-large-cap-gainers"][0]["causation_signal_event_id"],
            signal["occurrences"][0]["event_id"],
        )
        watchlists = WatchlistRuntime().resolve(
            self.configuration,
            [candidate],
            as_of=at,
            publish_targets=False,
            admissions_by_watchlist=admissions,
        )
        destination = next(
            row
            for row in watchlists["watchlists"]
            if row["watchlist_id"] == "top-large-cap-gainers"
        )
        self.assertEqual(destination["member_count"], 1)
        self.assertEqual(
            destination["members"][0]["causation_signal_event_id"],
            signal["occurrences"][0]["event_id"],
        )

    def test_missing_candidate_rearms_edge_for_later_return(self) -> None:
        runtime = SignalStreamRuntime()
        start = datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        candidate = {"ticker": "AAA", "change_pct": 4.5, "market_cap": 500_000_000}
        runtime.resolve(self.configuration, [candidate], as_of=start, journal=self.journal)
        runtime.resolve(self.configuration, [], as_of=start + timedelta(seconds=1), journal=self.journal)
        returned = runtime.resolve(self.configuration, [candidate], as_of=start + timedelta(seconds=2), journal=self.journal)
        self.assertEqual(returned["signal_streams"][0]["emitted_count"], 1)
        self.assertEqual(returned["occurrence_count"], 2)

    def test_occurrence_freezes_the_configured_interval_column_value(self) -> None:
        discovery = self.configuration["market_discovery"]
        column = next(row for row in discovery["column_catalog"] if row.get("source_id") == "price_change_pct")
        stream = discovery["signal_streams"][0]
        stream["columns"] = ["symbol", column["column_id"]]
        stream["column_intervals"] = {column["column_id"]: "5m"}

        result = SignalStreamRuntime().resolve(
            self.configuration,
            [{"ticker": "AAA", "change_pct": 4.5, "technical__price_change_pct__5m": 7.25}],
            as_of=datetime(2026, 8, 17, 15, 0, tzinfo=UTC),
            journal=self.journal,
        )

        self.assertEqual(result["occurrences"][0][column["column_id"]], 7.25)
        instance_ref = f"{column['field_ref']}@@5m"
        self.assertEqual(result["occurrences"][0]["field_evidence"][instance_ref]["value"], 7.25)
        self.assertEqual(result["occurrences"][0]["field_evidence"][instance_ref]["interval"], "5m")

    def test_watchlist_source_limits_signal_candidates_to_current_members(self) -> None:
        stream = self.configuration["market_discovery"]["signal_streams"][0]
        stream.update({"source_type": "watchlist", "source_id": "source-watchlist"})
        result = SignalStreamRuntime().resolve(
            self.configuration,
            [
                {"ticker": "AAA", "change_pct": 4.5, "market_cap": 500_000_000},
                {"ticker": "BBB", "change_pct": 5.0, "market_cap": 600_000_000},
            ],
            as_of=datetime(2026, 8, 17, 15, 0, tzinfo=UTC),
            journal=self.journal,
            watchlist_runtime={"watchlists": [{"watchlist_id": "source-watchlist", "members": [{"ticker": "BBB"}]}]},
        )

        self.assertEqual(result["signal_streams"][0]["candidate_count"], 1)
        self.assertEqual([row["ticker"] for row in result["occurrences"]], ["BBB"])

    def test_core_source_uses_complete_candidate_population(self) -> None:
        result = SignalStreamRuntime().resolve(
            self.configuration,
            [
                {"ticker": "AAA", "change_pct": 4.5, "market_cap": 500_000_000},
                {"ticker": "BBB", "change_pct": 5.0, "market_cap": 600_000_000},
            ],
            as_of=datetime(2026, 8, 17, 15, 0, tzinfo=UTC),
            journal=self.journal,
            watchlist_runtime={"watchlists": []},
        )

        self.assertEqual(result["signal_streams"][0]["candidate_count"], 2)
        self.assertEqual({row["ticker"] for row in result["occurrences"]}, {"AAA", "BBB"})

    def test_session_window_restarts_edges_and_only_returns_current_day(self) -> None:
        runtime = SignalStreamRuntime()
        candidate = {"ticker": "AAA", "change_pct": 4.5, "market_cap": 500_000_000}
        first_day = datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        second_day = first_day + timedelta(days=1)

        runtime.resolve(self.configuration, [candidate], as_of=first_day, journal=self.journal)
        next_session = runtime.resolve(
            self.configuration, [candidate], as_of=second_day, journal=self.journal
        )
        after_hours = runtime.snapshot(
            self.journal,
            as_of=datetime(2026, 8, 19, 1, 0, tzinfo=UTC),
            configuration=self.configuration,
        )

        self.assertEqual(next_session["signal_streams"][0]["emitted_count"], 1)
        self.assertEqual(next_session["occurrence_count"], 1)
        self.assertEqual(next_session["occurrences"][0]["event_time"], second_day.isoformat())
        self.assertFalse(after_hours["session"]["active"])
        self.assertEqual(after_hours["occurrences"], [])

    def test_weekend_is_not_an_active_signal_stream_session(self) -> None:
        session = signal_stream_session(datetime(2026, 8, 16, 15, 0, tzinfo=UTC))

        self.assertFalse(session["is_trading_day"])
        self.assertFalse(session["active"])

    def test_current_session_snapshot_coalesces_repeated_journal_reads(self) -> None:
        runtime = SignalStreamRuntime()
        session = {
            "session_key": "2026-08-19",
            "active": True,
            "is_trading_day": True,
            "start_at": datetime(2026, 8, 19, 8, 0, tzinfo=UTC),
            "end_at": datetime(2026, 8, 20, 0, 0, tzinfo=UTC),
        }
        with patch(
            "src.backend.signal_stream_runtime_service.signal_stream_session",
            return_value=session,
        ), patch.object(
            self.journal,
            "signal_stream_records",
            wraps=self.journal.signal_stream_records,
        ) as records:
            first = runtime.snapshot(self.journal, configuration=self.configuration)
            second = runtime.snapshot(self.journal, configuration=self.configuration)

        self.assertIs(first, second)
        records.assert_called_once()

    def test_live_snapshot_reuses_occurrences_loaded_by_evaluator(self) -> None:
        runtime = SignalStreamRuntime()
        at = datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        session = signal_stream_session(at)
        runtime.resolve(
            self.configuration,
            [{"ticker": "AAA", "change_pct": 4.5, "market_cap": 500_000_000}],
            as_of=at,
            journal=self.journal,
        )

        with patch(
            "src.backend.signal_stream_runtime_service.signal_stream_session",
            return_value=session,
        ), patch.object(
            self.journal,
            "signal_stream_records",
            side_effect=AssertionError("Canvas must not re-read the live journal"),
        ):
            snapshot = runtime.snapshot(
                self.journal,
                signal_stream_id="positive-move-signals",
                configuration=self.configuration,
            )

        self.assertEqual(snapshot["occurrence_count"], 1)
        self.assertEqual(snapshot["occurrences"][0]["ticker"], "AAA")

    def test_snapshot_does_not_wait_for_full_universe_evaluation_lock(self) -> None:
        runtime = SignalStreamRuntime()
        lock_held = threading.Event()
        release_lock = threading.Event()
        result: list[dict[str, object]] = []

        def hold_runtime_lock() -> None:
            with runtime._lock:
                lock_held.set()
                release_lock.wait(timeout=2)

        holder = threading.Thread(target=hold_runtime_lock)
        holder.start()
        self.assertTrue(lock_held.wait(timeout=1))
        session = {
            "session_key": "2026-08-19",
            "active": True,
            "is_trading_day": True,
            "start_at": datetime(2026, 8, 19, 8, 0, tzinfo=UTC),
            "end_at": datetime(2026, 8, 20, 0, 0, tzinfo=UTC),
        }
        with patch(
            "src.backend.signal_stream_runtime_service.signal_stream_session",
            return_value=session,
        ):
            reader = threading.Thread(
                target=lambda: result.append(runtime.snapshot(self.journal, configuration=self.configuration))
            )
            reader.start()
            reader.join(timeout=0.5)
            self.assertFalse(reader.is_alive(), "Canvas snapshot waited for the evaluation lock")
        release_lock.set()
        holder.join(timeout=1)
        self.assertEqual(result[0]["status"], "ready")

    @patch("src.backend.signal_stream_runtime_service.publish_computation_target")
    def test_core_source_leases_rules_and_frozen_evidence_for_all_candidates(self, publish) -> None:
        self.configuration["market_discovery"]["signal_streams"][0]["inclusion_rule_sets"] = [
            "watchlist-squeeze-early-impulse-100ms"
        ]
        seeds = SignalStreamRuntime().seed_computation_targets(
            self.configuration,
            [{"ticker": "AAA"}, {"ticker": "BBB"}],
        )

        self.assertEqual(seeds[0]["candidate_count"], 2)
        self.assertIn("core_bars", seeds[0]["capabilities"])
        self.assertEqual(publish.call_args.args[0], "signal-stream:positive-move-signals")
        self.assertEqual(publish.call_args.args[1], ["AAA", "BBB"])
        self.assertEqual(publish.call_args.kwargs["scope"], "signal_stream")

    @patch("src.backend.signal_stream_runtime_service.publish_computation_target")
    def test_configured_bar_column_is_included_in_signal_computation_demand(self, publish) -> None:
        discovery = self.configuration["market_discovery"]
        stream = discovery["signal_streams"][0]
        bar_change = next(
            row for row in discovery["column_catalog"]
            if row.get("column_id") == "field__price__change"
        )
        stream.update({
            "columns": ["symbol", bar_change["column_id"]],
            "column_intervals": {bar_change["column_id"]: "100ms"},
            "inclusion_rule_sets": ["watchlist-positive-gainer"],
        })

        seeds = SignalStreamRuntime().seed_computation_targets(
            self.configuration,
            [{"ticker": "AAA"}],
        )

        self.assertEqual(seeds[0]["timeframes"], ["100ms"])
        self.assertIn("core_bars", seeds[0]["capabilities"])
        self.assertEqual(publish.call_args.args[1], ["AAA"])

    @patch("src.backend.signal_stream_runtime_service.publish_computation_target")
    def test_watchlist_source_leases_only_current_members(self, publish) -> None:
        stream = self.configuration["market_discovery"]["signal_streams"][0]
        stream.update({
            "source_type": "watchlist",
            "source_id": "source-watchlist",
            "inclusion_rule_sets": ["watchlist-squeeze-early-impulse-100ms"],
        })
        seeds = SignalStreamRuntime().seed_computation_targets(
            self.configuration,
            [{"ticker": "AAA"}, {"ticker": "BBB"}],
            watchlist_runtime={"watchlists": [{"watchlist_id": "source-watchlist", "members": [{"ticker": "BBB"}]}]},
        )

        self.assertEqual(seeds[0]["candidate_count"], 1)
        self.assertEqual(publish.call_args.args[1], ["BBB"])


if __name__ == "__main__":
    unittest.main()
