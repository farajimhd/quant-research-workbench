from __future__ import annotations

import csv
import unittest
import tempfile
from pathlib import Path

import numpy as np

from research.news_reaction_model.price_bucket_pnl import AnchorPricePnlBreakdown, write_anchor_price_pnl_csv


class AnchorPricePnlBreakdownTests(unittest.TestCase):
    def test_boundaries_and_long_short_pnl_are_exact(self) -> None:
        breakdown = AnchorPricePnlBreakdown()
        breakdown.add(
            side=np.asarray([1, -1, 1, -1, 0, 1, -1, 1], dtype=np.int8),
            pnl=np.asarray([0.10, 0.25, -0.50, 1.00, 0.00, 2.00, -3.00, 9.00]),
            anchor_prices=np.asarray([0.50, 1.00, 19.99, 20.00, 99.99, 100.00, 250.00, np.nan]),
        )
        summary = breakdown.summary()
        buckets = summary["buckets"]

        self.assertEqual(buckets["penny_under_1"]["long"]["positions"], 1)
        self.assertAlmostEqual(buckets["penny_under_1"]["long"]["one_share_pnl"], 0.10)
        self.assertEqual(buckets["small_1_to_20"]["short"]["positions"], 1)
        self.assertEqual(buckets["small_1_to_20"]["long"]["positions"], 1)
        self.assertAlmostEqual(buckets["small_1_to_20"]["one_share_pnl"], -0.25)
        self.assertEqual(buckets["mid_20_to_100"]["short"]["positions"], 1)
        self.assertEqual(buckets["mid_20_to_100"]["abstained"], 1)
        self.assertAlmostEqual(buckets["mid_20_to_100"]["short"]["one_share_pnl"], 1.00)
        self.assertEqual(buckets["large_100_plus"]["active_positions"], 2)
        self.assertAlmostEqual(buckets["large_100_plus"]["long"]["one_share_pnl"], 2.00)
        self.assertAlmostEqual(buckets["large_100_plus"]["short"]["one_share_pnl"], -3.00)
        self.assertEqual(summary["unclassified_labels"], 1)

    def test_mismatched_inputs_fail_loudly(self) -> None:
        with self.assertRaises(ValueError):
            AnchorPricePnlBreakdown().add(
                side=np.asarray([1]),
                pnl=np.asarray([1.0, 2.0]),
                anchor_prices=np.asarray([10.0]),
            )

    def test_csv_exposes_each_bucket_and_side(self) -> None:
        breakdown = AnchorPricePnlBreakdown()
        breakdown.add(np.asarray([1, -1]), np.asarray([1.5, -0.5]), np.asarray([5.0, 150.0]))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "price-pnl.csv"
            write_anchor_price_pnl_csv(path, [("1m", breakdown)])
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1]["price_bucket"], "small_1_to_20")
        self.assertEqual(rows[1]["long_positions"], "1")
        self.assertEqual(rows[3]["short_positions"], "1")


if __name__ == "__main__":
    unittest.main()
