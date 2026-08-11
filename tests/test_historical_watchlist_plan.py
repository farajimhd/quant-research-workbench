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
            "sha256:95047e760497de35bf7bcb95d8d0703adf7093a8ddaeb7772e9f443245eb5ce0",
        )
        self.assertEqual(plan["qmd_sources"], ["liquidity-rank"])
        self.assertEqual(plan["external_features"][0]["field_id"], "reference.float_shares")
        self.assertEqual(plan["external_features"][0]["query_plan_id"], "reference.scanner_asof.v1")
        self.assertEqual(plan["chunk_duration_ms"], plan["cadence_ms"] * 1_800)
        self.assertTrue(plan["state_carry_required"])

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
