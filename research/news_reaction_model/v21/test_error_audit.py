from __future__ import annotations

import datetime as dt
import unittest

import numpy as np

from research.news_reaction_model.v21.error_audit import (
    Prediction,
    classify_error,
    generated_expiry_on_non_session,
    minute_bars,
    select_stratified_errors,
)


def prediction(
    index: int,
    *,
    family: str,
    actual: int,
    predicted: int,
    confidence: float,
) -> Prediction:
    probabilities = [0.05, 0.05, 0.05]
    probabilities[predicted] = confidence
    remainder = 1.0 - confidence
    for class_index in range(3):
        if class_index != predicted:
            probabilities[class_index] = remainder / 2.0
    return Prediction(
        row_index=index,
        episode_id=f"episode-{index}",
        canonical_news_id=f"news-{index}",
        ticker=f"T{index}",
        published_at_utc="2026-01-02T14:00:00Z",
        root_family=family,
        role="root",
        actual=actual,
        predicted=predicted,
        probabilities=tuple(probabilities),
        expected_return_pct=0.0,
        expected_upside_pct=5.0,
        expected_downside_pct=5.0,
        signed_opportunity_pct=5.0 if actual == 1 else -5.0,
    )


class ErrorAuditTest(unittest.TestCase):
    def test_generated_expiry_defect_requires_20_et_on_non_session(self) -> None:
        sessions = {dt.date(2026, 5, 15), dt.date(2026, 5, 18)}
        self.assertTrue(
            generated_expiry_on_non_session(
                dt.datetime(2026, 5, 16, 20, 0),
                sessions,
            )
        )
        self.assertFalse(
            generated_expiry_on_non_session(
                dt.datetime(2026, 5, 16, 13, 0),
                sessions,
            )
        )
        self.assertFalse(
            generated_expiry_on_non_session(
                dt.datetime(2026, 5, 18, 20, 0),
                sessions,
            )
        )

    def test_error_taxonomy(self) -> None:
        self.assertEqual(classify_error(1, 0), "false_upside")
        self.assertEqual(classify_error(2, 0), "false_downside")
        self.assertEqual(classify_error(0, 1), "missed_upside")
        self.assertEqual(classify_error(0, 2), "missed_downside")
        self.assertEqual(classify_error(1, 2), "reversed_upside")
        self.assertEqual(classify_error(2, 1), "reversed_downside")
        self.assertEqual(classify_error(1, 1), "correct")

    def test_selection_uses_unique_episodes_and_multiple_families(self) -> None:
        rows = [
            prediction(
                index,
                family=("company" if index % 2 == 0 else "analyst"),
                actual=(index % 3),
                predicted=((index + 1) % 3),
                confidence=0.9 - index * 0.001,
            )
            for index in range(40)
        ]
        selected = select_stratified_errors(rows, count=20)
        self.assertEqual(len(selected), 20)
        self.assertEqual(len({row.episode_id for row in selected}), 20)
        self.assertEqual({row.root_family for row in selected}, {"company", "analyst"})

    def test_minute_bars_use_last_for_open_close_and_extrema_for_range(self) -> None:
        minute = 1_700_000_000_000_000 // 60_000_000
        base = minute * 60_000_000
        events = np.asarray(
            [
                [base + 1, 1, 10.0, 100, 9.9, 10.1, 1, 1],
                [base + 2, 2, 12.0, 10, 9.9, 10.1, 0, 1],
                [base + 3, 3, 10.5, 50, 10.4, 10.6, 1, 1],
            ],
            dtype=np.float64,
        )
        bars = minute_bars(events)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].open, 10.0)
        self.assertEqual(bars[0].close, 10.5)
        self.assertEqual(bars[0].high, 12.0)
        self.assertEqual(bars[0].low, 10.0)
        self.assertEqual(bars[0].volume, 150.0)


if __name__ == "__main__":
    unittest.main()
