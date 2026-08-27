from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import UTC, datetime

from src.backend.historical_watchlist_plan import (
    compile_historical_watchlist_plan,
    compile_signal_stream_recovery_templates,
)
from src.backend.replay_run_service import (
    _historical_core_signal_plans_for_configuration,
    _historical_watchlist_plans_for_configuration,
)
from src.backend.trading_configuration_service import _default_draft


class HistoricalWatchlistPlanTests(unittest.TestCase):
    def test_event_native_signal_recovery_uses_persisted_qmd_occurrences(self) -> None:
        templates = compile_signal_stream_recovery_templates(
            _default_draft(),
            start=datetime(2026, 8, 7, 8, 0, tzinfo=UTC),
            end=datetime(2026, 8, 8, 0, 0, tzinfo=UTC),
        )

        squeeze = next(
            row for row in templates if row["signal_stream_id"] == "price-squeeze-5m"
        )
        early = next(
            row for row in templates if row["signal_stream_id"] == "price-squeeze-early"
        )
        self.assertEqual(squeeze["recovery_kind"], "source_native")
        self.assertEqual(early["recovery_kind"], "source_native")

        halt = next(row for row in templates if row["signal_stream_id"] == "market-halts")
        news = next(row for row in templates if row["signal_stream_id"] == "bullish-news-v1")
        synthesis_news = next(
            row
            for row in templates
            if row["signal_stream_id"] == "bullish-synthesis-deepfm-news-v1"
        )
        self.assertEqual(halt["recovery_kind"], "source_native")
        self.assertEqual(news["recovery_kind"], "source_native")
        self.assertEqual(synthesis_news["recovery_kind"], "source_native")

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
            "sha256:16d7a19abbb2215bd22dc36365fdb2922e0aea776a3b0f5d0d6fe6ee7ca8abb2",
        )
        self.assertEqual(plan["qmd_sources"], ["market.liquidity_rank"])
        self.assertEqual(
            plan["qmd_source_specs"],
            [{
                "instance_id": "market.liquidity_rank",
                "source_id": "market.liquidity_rank",
                "runtime_field": "liquidity_rank",
                "interval": "",
                "aggregation": "",
            }],
        )
        self.assertEqual(plan["external_features"][0]["field_id"], "reference.float_shares")
        self.assertEqual(plan["external_features"][0]["query_plan_id"], "reference.scanner_asof.v1")
        self.assertEqual(plan["external_features"][0]["query_plan_version"], 3)
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

    def test_episode_only_field_is_not_misrepresented_as_historical_bar_input(self) -> None:
        configuration = _default_draft()
        watchlist = next(
            row
            for row in configuration["market_discovery"]["watchlists"]
            if row["watchlist_id"] == "core-candidates"
        )
        watchlist["inclusion_rule_sets"] = ["signal-price-squeeze-5m"]
        with self.assertRaises(ValueError):
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

        self.assertEqual(plan["schema_version"], 4)
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

    def test_resolved_run_plan_watchlist_compiles_without_legacy_universes(self) -> None:
        model = _default_draft()
        approved = {
            "payload": {
                "run_plan": {"watchlist_ids": ["squeeze-tradable-candidates"]},
                "universe": {
                    "source": "watchlist",
                    "watchlist_snapshots": [{
                        "watchlist_id": "squeeze-tradable-candidates",
                        "name": "Squeeze tradable candidates",
                    }],
                },
            },
            "configuration_model": model,
        }

        plans = _historical_watchlist_plans_for_configuration(
            approved,
            start=datetime(2026, 8, 21, 8, tzinfo=UTC),
            end=datetime(2026, 8, 22, 0, tzinfo=UTC),
        )

        self.assertEqual([row["watchlist_id"] for row in plans], ["squeeze-tradable-candidates"])
        self.assertTrue({
            "market.liquidity_score",
            "market.session_dollar_volume",
            "market.trade_rate_10s",
        }.issubset(plans[0]["qmd_sources"]))

    def test_source_native_signal_is_not_reconstructed_from_bar_fields(self) -> None:
        model = _default_draft()
        stream = next(
            row
            for row in model["market_discovery"]["signal_streams"]
            if row["signal_stream_id"] == "price-squeeze-5m"
        )
        approved = {
            "payload": {"signal_activation": {"signal_streams": [stream]}},
            "configuration_model": model,
        }

        plans = _historical_core_signal_plans_for_configuration(
            approved,
            start=datetime(2026, 8, 21, 8, tzinfo=UTC),
            end=datetime(2026, 8, 22, 0, tzinfo=UTC),
        )

        self.assertEqual(plans, [])


if __name__ == "__main__":
    unittest.main()
