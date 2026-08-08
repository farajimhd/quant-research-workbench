from __future__ import annotations

import unittest

from .sol_teacher_forecast_split import build_article_grouped_split


class SolTeacherForecastSplitTests(unittest.TestCase):
    def test_split_is_deterministic_chronological_and_article_grouped(self) -> None:
        articles = {
            f"S{index:05d}": {
                "sample_id": f"S{index:05d}",
                "source_timestamp": f"2026-01-{index:02d}T12:00:00Z",
            }
            for index in range(1, 11)
        }
        units = [
            {"sample_id": sample_id, "unit_id": f"{sample_id}::AAA"}
            for sample_id in articles
        ]
        units.append({"sample_id": "S00003", "unit_id": "S00003::BBB"})

        first = build_article_grouped_split(articles, units)
        second = build_article_grouped_split(dict(reversed(list(articles.items()))), units)

        self.assertEqual(first, second)
        self.assertEqual(len(first["audit_article_ids"]), 7)
        self.assertEqual(len(first["test_article_ids"]), 3)
        self.assertFalse(
            set(first["audit_article_ids"]) & set(first["test_article_ids"])
        )
        self.assertNotEqual(
            "S00003" in first["audit_article_ids"],
            "S00003" in first["test_article_ids"],
        )

    def test_split_rejects_article_and_unit_population_mismatch(self) -> None:
        articles = {
            "S00001": {
                "sample_id": "S00001",
                "source_timestamp": "2026-01-01T12:00:00Z",
            }
        }
        with self.assertRaisesRegex(RuntimeError, "do not agree"):
            build_article_grouped_split(
                articles,
                [{"sample_id": "S00002", "unit_id": "S00002::AAA"}],
            )


if __name__ == "__main__":
    unittest.main()
