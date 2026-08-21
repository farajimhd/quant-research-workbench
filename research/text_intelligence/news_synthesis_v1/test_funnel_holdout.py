from __future__ import annotations

import unittest

from .funnel_holdout import select_uniform_holdout


class FunnelHoldoutTests(unittest.TestCase):
    def test_selection_is_order_invariant_and_deterministic(self) -> None:
        rows = [
            {"source_id": f"s-{index}", "published_at_utc": f"2026-08-14T00:00:{index:02d}Z"}
            for index in range(10)
        ]
        first = select_uniform_holdout(rows, sample_size=4, seed="fixed")
        second = select_uniform_holdout(list(reversed(rows)), sample_size=4, seed="fixed")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 4)

    def test_selection_rejects_duplicate_ids(self) -> None:
        rows = [
            {"source_id": "same", "published_at_utc": "2026-08-14T00:00:00Z"},
            {"source_id": "same", "published_at_utc": "2026-08-14T00:00:01Z"},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            select_uniform_holdout(rows, sample_size=1)
