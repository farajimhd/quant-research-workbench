from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.validate_qmd_authority import collect_evidence, validate_event_page, validate_source_plan


class QmdAuthorityValidationTests(unittest.TestCase):
    def test_source_plan_requires_exact_non_overlapping_tiling(self) -> None:
        plan = {
            "plan_hash": "plan-1",
            "event_schema_version": 4,
            "segments": [
                {"start": "2026-08-01T00:00:00Z", "end": "2026-08-02T00:00:00Z", "tier": "archive"},
                {"start": "2026-08-02T00:00:00Z", "end": "2026-08-03T00:00:00Z", "tier": "recent"},
            ],
        }
        self.assertEqual(
            validate_source_plan(plan, start="2026-08-01T00:00:00Z", end="2026-08-03T00:00:00Z"),
            [],
        )
        plan["segments"][1]["start"] = "2026-08-01T23:59:00Z"
        self.assertRegex(validate_source_plan(plan, start="2026-08-01T00:00:00Z", end="2026-08-03T00:00:00Z")[0], "expected")

    def test_event_validation_rejects_order_regression_and_missing_lineage(self) -> None:
        failures, _, lineage = validate_event_page(
            [
                {"ts": "2026-08-01T00:00:02Z", "ticker": "AAPL", "raw": {"correlation_id": "c", "causation_id": "a"}},
                {"ts": "2026-08-01T00:00:01Z", "ticker": "AAPL", "raw": {}},
            ],
            None,
        )
        self.assertEqual(lineage, 1)
        self.assertTrue(any("regressed" in failure for failure in failures))
        self.assertTrue(any("lacks" in failure for failure in failures))

    @patch("scripts.validate_qmd_authority._get_json")
    def test_collection_pins_revision_across_pages(self, get_json) -> None:
        plan = {
            "plan_hash": "plan-1",
            "event_schema_version": 4,
            "segments": [{"start": "2026-08-01T00:00:00Z", "end": "2026-08-02T00:00:00Z", "tier": "archive"}],
        }
        revision = {"source_plan_hash": "plan-1", "token": "revision-1"}
        event = {"ts": "2026-08-01T15:00:00Z", "ticker": "AAPL", "raw": {"correlation_id": "c", "causation_id": "a"}}
        responses = [
            {"running": True, "status": "closed"},
            {"service": "qmd_history_gateway", "status": "ready"},
            {"header": {"service": "qmd_gateway"}},
            {"header": {"service": "qmd_history_gateway"}},
            plan,
            {"complete": True},
            {"complete": True, "events": [event], "next_cursor": None, "source_revision": revision},
        ]
        get_json.side_effect = responses
        report = collect_evidence(
            live_url="http://live",
            history_url="http://history",
            start="2026-08-01T00:00:00Z",
            end="2026-08-02T00:00:00Z",
            tickers="AAPL",
            page_size=100,
            max_events=100,
        )
        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(report["event_page_proof"]["lineage_count"], 1)


if __name__ == "__main__":
    unittest.main()
