from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import UTC, datetime

from src.backend.historical_watchlist_plan import compile_historical_watchlist_plan
from src.backend.replay_run_service import _historical_watchlist_plans_for_configuration
from src.backend.trading_configuration_service import _default_draft


class HistoricalWatchlistPlanTests(unittest.TestCase):
    def test_compiles_deterministic_qmd_and_external_feature_contract(self) -> None:
        configuration = _default_draft()
        watchlist = next(row for row in configuration["market_discovery"]["watchlists"] if row["watchlist_id"] == "core-candidates")
        watchlist["inclusion_rule_sets"] = ["watchlist-float-small"]
        plan = compile_historical_watchlist_plan(
            configuration,
            "core-candidates",
            start=datetime(2026, 8, 7, 13, 30, tzinfo=UTC),
            end=datetime(2026, 8, 7, 20, 0, tzinfo=UTC),
        )
        repeated = compile_historical_watchlist_plan(
            deepcopy(configuration),
            "core-candidates",
            start=datetime(2026, 8, 7, 13, 30, tzinfo=UTC),
            end=datetime(2026, 8, 7, 20, 0, tzinfo=UTC),
        )

        self.assertEqual(plan["plan_hash"], repeated["plan_hash"])
        self.assertEqual(
            plan["plan_hash"],
            "sha256:0bb79bec181d384705ca5e6bc101f2c646f3e802a65d7cc0feb5845b9ac91840",
        )
        self.assertEqual(plan["qmd_sources"], ["market.liquidity_rank"])
        self.assertEqual(plan["external_features"][0]["field_id"], "reference.float_shares")
        self.assertEqual(plan["external_features"][0]["query_plan_id"], "reference.scanner_asof.v1")
        self.assertEqual(plan["external_features"][0]["query_plan_version"], 2)
        self.assertEqual(plan["chunk_duration_ms"], plan["cadence_ms"] * 1_800)
        self.assertTrue(plan["state_carry_required"])

    def test_rejects_unsupported_value_selection(self) -> None:
        configuration = _default_draft()
        watchlist = next(row for row in configuration["market_discovery"]["watchlists"] if row["watchlist_id"] == "core-candidates")
        watchlist["inclusion_rule_sets"] = ["watchlist-float-small"]
        rule = next(row for row in configuration["market_discovery"]["rule_sets"] if row["rule_set_id"] == "watchlist-float-small")
        rule["conditions"][0]["left_value_selection"] = "oldest"
        with self.assertRaisesRegex(ValueError, "supports only latest left value selection"):
            compile_historical_watchlist_plan(
                configuration,
                "core-candidates",
                start=datetime(2026, 8, 7, 13, 30, tzinfo=UTC),
                end=datetime(2026, 8, 7, 20, 0, tzinfo=UTC),
            )

    def test_rejects_deferred_intelligence_source(self) -> None:
        configuration = _default_draft()
        watchlist = next(row for row in configuration["market_discovery"]["watchlists"] if row["watchlist_id"] == "core-candidates")
        watchlist["ranking_field"] = "signal.news_labeled"

        with self.assertRaisesRegex(ValueError, "not causally available"):
            compile_historical_watchlist_plan(
                configuration,
                "core-candidates",
                start=datetime(2026, 8, 7, 13, 30, tzinfo=UTC),
                end=datetime(2026, 8, 7, 20, 0, tzinfo=UTC),
            )

    def test_rejects_naive_or_reversed_bounds(self) -> None:
        configuration = _default_draft()
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            compile_historical_watchlist_plan(
                configuration,
                "core-candidates",
                start=datetime(2026, 8, 7, 13, 30),
                end=datetime(2026, 8, 7, 20, 0),
            )

    def test_multi_session_plan_excludes_overnight_and_weekend_cadence(self) -> None:
        configuration = _default_draft()
        plan = compile_historical_watchlist_plan(
            configuration,
            "core-candidates",
            start=datetime(2026, 8, 7, 8, 0, tzinfo=UTC),
            end=datetime(2026, 8, 11, 0, 0, tzinfo=UTC),
        )

        self.assertEqual(plan["schema_version"], 3)
        self.assertEqual(plan["focused_seed_multiplier"], 5)
        self.assertEqual(len(plan["evaluation_windows"]), 2)
        self.assertEqual(plan["evaluation_windows"][0]["start"], "2026-08-07T04:00:00-04:00")
        self.assertEqual(plan["evaluation_windows"][1]["start"], "2026-08-10T04:00:00-04:00")

    def test_approved_runtime_universe_compiles_exact_watchlist_plan(self) -> None:
        model = _default_draft()
        approved = {
            "payload": {
                "universes": [{
                    "enabled": True,
                    "name": "Core candidates",
                    "scanner_view_id": "core-candidates",
                    "source": "watchlist",
                }]
            },
            "configuration_model": model,
        }
        plans = _historical_watchlist_plans_for_configuration(
            approved,
            start=datetime(2026, 8, 7, 13, 30, tzinfo=UTC),
            end=datetime(2026, 8, 7, 20, 0, tzinfo=UTC),
        )

        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["watchlist_id"], "core-candidates")
        self.assertTrue(plans[0]["plan_hash"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
