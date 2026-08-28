from __future__ import annotations

import unittest
import os
import tempfile
from datetime import UTC, datetime
from unittest.mock import patch

from src.backend.historical_watchlist_feature_service import (
    _durable_cache_read,
    _durable_cache_write,
    _enrich_materialized_identity,
    _materialization_source_bounds,
    historical_watchlist_external_feature_bundle,
    historical_watchlist_external_feature_intervals,
    materialize_historical_watchlist_plans,
)
from src.backend.query_plans.historical_watchlist_feature_intervals_v1 import (
    MAX_CHANGE_CLOCKS,
    feature_change_clocks,
)


class _Client:
    def execute(self, _sql: str) -> str:
        return '{"available_at":"2026-08-07T13:31:00.000000Z"}\n'


class HistoricalWatchlistFeatureServiceTests(unittest.TestCase):
    def test_materialization_source_revision_stops_at_last_evaluation_window(self) -> None:
        start, end = _materialization_source_bounds([{
            "start": "2026-08-21T08:00:00+00:00",
            "end": "2026-08-22T00:00:00+00:00",
            "evaluation_windows": [
                {"start": "2026-08-21T08:01:00+00:00", "end": "2026-08-21T08:01:01+00:00"},
                {"start": "2026-08-21T13:29:58+00:00", "end": "2026-08-21T13:29:59+00:00"},
            ],
        }])

        self.assertEqual(start, "2026-08-21T08:00:00+00:00")
        self.assertEqual(end, "2026-08-21T13:29:59+00:00")

    def test_identity_unavailable_members_are_rejected_with_explicit_evidence(self) -> None:
        materialized = {
            "materialization_id": "sha256:qmd",
            "transition_count": 3,
            "chunks": [{"transitions": [
                {"effective_at": "2026-08-21T08:00:09+00:00", "event": "added", "ticker": "KORU"},
                {"effective_at": "2026-08-21T08:00:10+00:00", "event": "added", "ticker": "AAPL"},
                {"effective_at": "2026-08-21T08:00:11+00:00", "event": "removed", "ticker": "KORU"},
            ]}],
        }

        _enrich_materialized_identity(
            materialized,
            identity_intervals=[{
                "ticker": "AAPL",
                "start": "2026-08-21T08:00:00+00:00",
                "end": "2026-08-21T09:00:00+00:00",
                "identity": {"ibkr_conid": 265598},
            }],
            identity_revision={"complete": True, "source_revision": "sha256:identity"},
        )

        transitions = materialized["chunks"][0]["transitions"]
        self.assertEqual([row["ticker"] for row in transitions], ["AAPL"])
        self.assertEqual(transitions[0]["identity"]["ibkr_conid"], 265598)
        self.assertEqual(materialized["identity_rejection_count"], 1)
        self.assertEqual(
            materialized["identity_rejections"][0]["reason"],
            "point_in_time_identity_unavailable",
        )
        self.assertEqual(materialized["qmd_transition_count"], 3)
        self.assertEqual(materialized["application_transition_count"], 1)

    def test_durable_cache_is_bound_to_exact_source_revision(self) -> None:
        key = "sha256:" + "a" * 64
        revision = {"source_plan_hash": "plan-1", "token": "revision-1"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"QMD_WATCHLIST_TIMELINE_CACHE_DIR": directory}
        ):
            _durable_cache_write(key, {"value": 7}, source_revision=revision)
            self.assertEqual(
                _durable_cache_read(key, source_revision=revision), {"value": 7}
            )
            self.assertIsNone(
                _durable_cache_read(
                    key,
                    source_revision={"source_plan_hash": "plan-1", "token": "revision-2"},
                )
            )

    @patch("src.backend.qmd_gateway_client.qmd_historical_source_revision")
    @patch("src.backend.qmd_gateway_client.qmd_materialize_historical_watchlist_timelines")
    @patch("src.backend.historical_watchlist_feature_service.historical_watchlist_external_feature_bundle")
    def test_batch_enriches_each_timeline_without_replaying_per_plan(
        self, bundle, materialize, source_revision
    ) -> None:
        revision = {
            "complete_for_history": True,
            "request_complete": True,
            "source_plan_hash": "source-plan-1",
            "token": "source-revision-1",
        }
        source_revision.return_value = revision
        bundle.side_effect = [
            {
                "external_feature_intervals": [],
                "external_feature_revisions": [],
                "identity_intervals": [{
                    "ticker": ticker,
                    "start": "2026-08-07T13:30:00+00:00",
                    "end": "2026-08-07T13:32:00+00:00",
                    "identity": {"ibkr_conid": conid},
                }],
                "identity_revision": {"complete": True, "source_revision": f"sha256:{ticker}"},
            }
            for ticker, conid in (("AAPL", 1), ("MSFT", 2))
        ]
        materialize.return_value = {
            "batch_materialization_id": "sha256:qmd-batch",
            "source_revision": revision,
            "materializations": [
                {
                    "watchlist_id": watchlist_id,
                    "plan_hash": f"sha256:{watchlist_id}",
                    "materialization_id": f"sha256:m-{watchlist_id}",
                    "projection_complete": True,
                    "projection_mode": "membership_transitions",
                    "projection_tickers": ["AAPL", "MSFT"],
                    "source_tickers": ["AAPL", "MSFT"],
                    "chunks": [{"transitions": [{
                        "effective_at": "2026-08-07T13:31:00+00:00",
                        "event": "added",
                        "ticker": ticker,
                    }]}],
                }
                for watchlist_id, ticker in (("one", "AAPL"), ("two", "MSFT"))
            ],
        }
        plans = [
            {
                "watchlist_id": value,
                "plan_hash": f"sha256:{value}-unique",
                "start": "2026-08-07T13:30:00+00:00",
                "end": "2026-08-07T13:32:00+00:00",
                "qmd_sources": [],
            }
            for value in ("one", "two")
        ]

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"QMD_WATCHLIST_TIMELINE_CACHE_DIR": directory}
        ):
            result = materialize_historical_watchlist_plans(
                plans,
                projection_tickers=["msft", "AAPL", "AAPL"],
            )

        self.assertEqual(materialize.call_count, 1)
        self.assertEqual(
            bundle.call_args_list[0].kwargs["identity_tickers"],
            ["AAPL", "MSFT"],
        )
        self.assertEqual(
            materialize.call_args.args[0][0]["projection_tickers"],
            ["AAPL", "MSFT"],
        )
        self.assertEqual(
            result["materializations"][1]["chunks"][0]["transitions"][0]["identity"]["ibkr_conid"],
            2,
        )
        self.assertEqual(
            result["materializations"][1]["assignment_identities"],
            [{"ticker": "MSFT", "ibkr_conid": 2}],
        )
        self.assertTrue(result["application_batch_materialization_id"].startswith("sha256:"))

    def setUp(self) -> None:
        self.plan = {
            "start": "2026-08-07T13:30:00+00:00",
            "end": "2026-08-07T13:32:00+00:00",
            "cadence_ms": 1_000,
            "evaluation_windows": [
                {
                    "start": "2026-08-07T09:30:00-04:00",
                    "end": "2026-08-07T09:32:00-04:00",
                }
            ],
            "external_features": [
                {
                    "field_id": "reference.float_shares",
                    "query_plan_id": "reference.scanner_asof.v1",
                    "query_plan_version": 2,
                    "schema_version": 1,
                },
                {
                    "field_id": "fundamental.trajectory_score",
                    "query_plan_id": "sec.fundamentals_asof.v1",
                    "query_plan_version": 1,
                    "schema_version": 1,
                },
            ],
        }

    def test_diffs_only_source_change_clocks_and_hashes_each_field(self) -> None:
        def reference(clock: datetime, **_kwargs):
            return {
                "AAPL": {
                    "ibkr_conid": 265598,
                    "symbol_id": "symbol-aapl",
                    "float_shares": 3_000_000
                    if clock < datetime(2026, 8, 7, 13, 31, tzinfo=UTC)
                    else 4_000_000
                }
            }

        def fundamentals(_clock: datetime, **_kwargs):
            return {"AAPL": {"financial_trajectory_score": 0.75}}

        revisions, intervals = historical_watchlist_external_feature_intervals(
            self.plan,
            client=_Client(),
            reference_projection=reference,
            fundamental_projection=fundamentals,
        )

        float_rows = [
            row for row in intervals if row["field_id"] == "reference.float_shares"
        ]
        trajectory_rows = [
            row
            for row in intervals
            if row["field_id"] == "fundamental.trajectory_score"
        ]
        self.assertEqual([row["value"] for row in float_rows], [3_000_000.0, 4_000_000.0])
        self.assertEqual(len(trajectory_rows), 1)
        self.assertEqual(trajectory_rows[0]["value"], 0.75)
        self.assertEqual([row["field_id"] for row in revisions], [
            "fundamental.trajectory_score",
            "reference.float_shares",
        ])
        self.assertTrue(all(row["complete"] for row in revisions))
        self.assertTrue(all(row["source_revision"].startswith("sha256:") for row in revisions))
        self.assertEqual(revisions[1]["query_plan_version"], 2)

        bundle = historical_watchlist_external_feature_bundle(
            self.plan,
            client=_Client(),
            reference_projection=reference,
            fundamental_projection=fundamentals,
        )
        self.assertEqual(len(bundle["identity_intervals"]), 1)
        self.assertEqual(
            bundle["identity_intervals"][0]["identity"]["ibkr_conid"],
            265598,
        )
        self.assertTrue(bundle["identity_revision"]["complete"])

    def test_change_clock_query_is_bounded_and_causal(self) -> None:
        sql = feature_change_clocks(
            cadence_ms=1_000,
            include_reference=True,
            include_fundamentals=True,
            start=datetime(2026, 8, 7, 13, 30, tzinfo=UTC),
            end=datetime(2026, 8, 7, 20, 0, tzinfo=UTC),
        )
        self.assertIn("market_security_float_v1", sql)
        self.assertIn("sec_xbrl_company_fact_v3", sql)
        self.assertIn(f"LIMIT {MAX_CHANGE_CLOCKS + 1}", sql)
        self.assertIn("available_at >", sql)
        self.assertIn("available_at <", sql)
        self.assertIn("greatest(observed_at_utc, inserted_at)", sql)
        self.assertIn("greatest(toDateTime64(effective_date", sql)

        identity_sql = feature_change_clocks(
            cadence_ms=1_000,
            include_reference=True,
            include_fundamentals=False,
            identity_tickers=["aapl", "MSFT"],
            start=datetime(2026, 8, 7, 13, 30, tzinfo=UTC),
            end=datetime(2026, 8, 7, 20, 0, tzinfo=UTC),
        )
        self.assertIn("upper(ticker) IN ('AAPL', 'MSFT')", identity_sql)
        self.assertNotIn("market_security_float_v1", identity_sql)

    def test_identity_only_projection_queries_only_strategy_tickers(self) -> None:
        observed_tickers = []

        def reference(_clock: datetime, **kwargs):
            observed_tickers.append(kwargs.get("tickers"))
            return {
                "AAPL": {
                    "ibkr_conid": 265598,
                    "symbol_id": "symbol-aapl",
                }
            }

        plan = {**self.plan, "external_features": []}
        bundle = historical_watchlist_external_feature_bundle(
            plan,
            client=_Client(),
            reference_projection=reference,
            identity_tickers=["AAPL"],
        )

        self.assertTrue(observed_tickers)
        self.assertTrue(all(tickers == ("AAPL",) for tickers in observed_tickers))
        self.assertEqual(len(bundle["identity_intervals"]), 1)


if __name__ == "__main__":
    unittest.main()
