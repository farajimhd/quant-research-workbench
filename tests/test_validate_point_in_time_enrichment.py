from __future__ import annotations

import unittest
from datetime import UTC, datetime

from scripts.validate_point_in_time_enrichment import nested, validate_snapshot


class PointInTimeEnrichmentAcceptanceTests(unittest.TestCase):
    def test_accepts_only_evidence_available_by_the_requested_cutoff(self) -> None:
        cutoff = datetime(2026, 8, 7, 15, tzinfo=UTC)
        payload = {
            "as_of": cutoff.isoformat(),
            "status": "ready",
            "facts": {
                "identity": {"universe_date": "2026-08-05"},
                "market": {"observed_at_utc": "2026-08-04 07:59:26.997"},
                "borrow": {"observed_at_utc": "2026-08-07 14:32:39.389"},
            },
            "freshness": {
                "borrow": {"available_at": "2026-08-07T14:32:39.389Z"}
            },
        }

        result = validate_snapshot(payload, cutoff)

        self.assertEqual(result["failures"], [])
        self.assertEqual(result["temporal_fields_checked"], 4)
        self.assertEqual(
            nested(payload, "facts.borrow.observed_at_utc"),
            "2026-08-07 14:32:39.389",
        )

    def test_fails_closed_on_future_evidence(self) -> None:
        cutoff = datetime(2026, 8, 7, 14, tzinfo=UTC)
        payload = {
            "as_of": cutoff.isoformat(),
            "status": "ready",
            "facts": {
                "borrow": {"observed_at_utc": "2026-08-07 14:32:39.389"}
            },
        }

        result = validate_snapshot(payload, cutoff)

        self.assertEqual(len(result["failures"]), 1)
        self.assertIn("future evidence", result["failures"][0])


if __name__ == "__main__":
    unittest.main()
