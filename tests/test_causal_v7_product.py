from __future__ import annotations

from datetime import date
import json
import unittest
from unittest.mock import patch
from uuid import UUID

from pipelines.market_sip.events.causal_v7_product import (
    SOURCE_POLICY, STATE_TABLE, LEVEL_TABLE, COVERAGE_TABLE,
    bar_cutoff, ddl, input_bar, materialize, publish,
)


class _Engine:
    def __init__(self) -> None:
        self.bars: list[dict[str, float]] = []

    def update(self, bar: dict[str, float], *, observed_at: int) -> None:
        assert bar["t"] == observed_at
        self.bars.append(bar)


def _projection(engine: _Engine, cutoff: int, provenance: dict, _segments: bool) -> dict:
    levels = [{"unified_level_id": "prior", "price": 1.0}]
    if len(engine.bars) >= 2:
        levels.append({"unified_level_id": "new", "price": 2.0})
    return {
        "as_of": cutoff,
        "max_input_timestamp": engine.bars[-1]["t"] if engine.bars else cutoff,
        "bars_processed": len(engine.bars),
        "provenance": provenance,
        "unified_levels": levels,
        "qmd_structure_unified_levels": levels,
    }


class CausalV7ProductTests(unittest.TestCase):
    def test_midnight_bucket_clock_and_0405_source_boundary(self) -> None:
        day = date(2026, 8, 18)
        self.assertEqual(bar_cutoff(day, 14_700), 1_787_040_301)
        with self.assertRaisesRegex(ValueError, "04:05"):
            bar_cutoff(day, 14_699)

    def test_sparse_levels_and_every_completed_input_state(self) -> None:
        day = date(2026, 8, 18)
        rows = [
            {"bucket_index": index, "price_valid": 1,
             "open_int": 10_000, "high_int": 10_000,
             "low_int": 10_000, "close_int": 10_000, "volume": 10.0}
            for index in (14_700, 14_703, 14_704)
        ]
        with patch("src.market_engine.v7_qmd.projection", side_effect=_projection):
            states, levels, input_hash = materialize(
                day=day, ticker="TEST", engine=_Engine(), bars=rows,
                provenance={"catalog_hash": "pinned"}, source_hash="a" * 64,
            )
        self.assertEqual([row["second_index"] for row in states],
                         [14_400, 14_701, 14_704, 14_705])
        self.assertEqual([row["second_index"] for row in levels], [14_400, 14_704])
        self.assertEqual([row["level_revision"] for row in states], [0, 0, 1, 1])
        self.assertEqual(len(input_hash), 64)
        self.assertEqual(json.loads(states[1]["metadata_json"])["source_audit"]["source_policy"],
                         SOURCE_POLICY)

    def test_rejects_bad_bar_geometry_and_storage_fallback(self) -> None:
        row = {"bucket_index": 14_700, "price_valid": 1,
               "open_int": 10_000, "high_int": 9_000,
               "low_int": 10_000, "close_int": 10_000, "volume": 1}
        with self.assertRaisesRegex(ValueError, "geometry"):
            input_bar(date(2026, 8, 18), row)
        self.assertTrue(all("storage_policy='live_market_ssd'" in statement for statement in ddl()))

    def test_publication_certifies_last_after_read_back(self) -> None:
        attempt = "00000000-0000-0000-0000-000000000001"
        written: list[str] = []

        def capture(_client, table: str, _rows: list[dict]) -> None:
            written.append(table)

        with (
            patch("pipelines.market_sip.events.causal_v7_product.published_coverage",
                  side_effect=[None, {"attempt_id": attempt}]),
            patch("pipelines.market_sip.events.causal_v7_product._insert_rows", side_effect=capture),
            patch("pipelines.market_sip.events.causal_v7_product._evidence",
                  side_effect=[{"n": 1, "hash": "11"}, {"n": 1, "hash": "22"}]),
            patch("pipelines.market_sip.events.causal_v7_product.uuid4", return_value=UUID(attempt)),
        ):
            status, observed = publish(
                object(), build_id="build", day=date(2026, 8, 18), ticker="TEST",
                bars_attempt=attempt, source_hash="a" * 64,
                seed_checkpoint_hash="seed", catalog_hash="catalog",
                producer_hash="b" * 64, input_hash="c" * 64,
                states=[{"second_index": 14_400, "level_revision": 0}],
                levels=[{"second_index": 14_400, "level_revision": 0}],
            )
        self.assertEqual((status, observed), ("completed", attempt))
        self.assertEqual(written, [STATE_TABLE, LEVEL_TABLE, COVERAGE_TABLE])


if __name__ == "__main__":
    unittest.main()
