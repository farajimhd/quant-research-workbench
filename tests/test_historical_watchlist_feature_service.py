from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from src.backend.historical_watchlist_feature_service import (
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
    @patch("src.backend.qmd_gateway_client.qmd_materialize_historical_watchlist_timelines")
    @patch("src.backend.historical_watchlist_feature_service.historical_watchlist_external_feature_bundle")
    def test_batch_enriches_each_timeline_without_replaying_per_plan(
        self, bundle, materialize
    ) -> None:
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
            "materializations": [
                {
                    "watchlist_id": watchlist_id,
                    "plan_hash": f"sha256:{watchlist_id}",
                    "materialization_id": f"sha256:m-{watchlist_id}",
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
            {"watchlist_id": value, "plan_hash": f"sha256:{value}-unique"}
            for value in ("one", "two")
        ]

        result = materialize_historical_watchlist_plans(plans)

        self.assertEqual(materialize.call_count, 1)
        self.assertEqual(
            result["materializations"][1]["chunks"][0]["transitions"][0]["identity"]["ibkr_conid"],
            2,
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


if __name__ == "__main__":
    unittest.main()
