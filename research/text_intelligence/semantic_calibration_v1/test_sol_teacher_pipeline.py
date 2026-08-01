from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from .comparison import CollectionItem
from .schema import stable_json_hash
from .sol_teacher_batch import (
    CounterUsage,
    TeacherBatchConfig,
    build_teacher_plan,
)
from .sol_teacher_corpus import (
    load_ground_truth_exclusion,
    select_teacher_candidates,
    source_year,
    ticker_scope,
)
from .storage import write_json_atomic


def _candidate(year: int, index: int) -> dict[str, object]:
    ticker_count = index % 3
    tickers = [f"T{index:04d}{offset}" for offset in range(ticker_count)]
    return {
        "source_id": f"{year}-{index:04d}",
        "source_timestamp": f"{year}-06-15 12:00:00.000000000",
        "event": {"tickers": tickers, "title": f"Article {index}", "teaser": ""},
        "v5_units": [
            {
                "content_role": ("primary_event", "analyst_event", "mover_recap")[index % 3],
                "source_origin": ("issuer_direct", "analyst_research", "editorial_aggregation")[index % 3],
                "semantic_direction": ("positive", "negative", "neutral")[index % 3],
                "event_concepts": [("guidance", "analyst_action", "market_movement")[index % 3]],
                "forecast_trigger_eligible": index % 3 == 0,
                "reaction_evaluation_eligible": index % 3 == 0,
                "issuer_history_context_eligible": True,
            }
        ],
    }


def _collection_item(index: int, candidates: int = 1) -> CollectionItem:
    sample_id = f"S{index:05d}"
    blinded = {
        "sample_id": sample_id,
        "source_id": f"source-{index}",
        "source_text_sha256": f"{index:064x}",
        "teacher_item_sha256": f"{index + 1:064x}",
        "publication": {
            "title": f"Title {index}",
            "teaser": "",
            "author": "",
            "provider_tags": [],
            "channels": [],
            "provider_tickers": [f"T{index:03d}"],
        },
        "point_in_time_issuer_candidates": [
            {
                "canonical_instrument_id": f"T{index:03d}{offset}",
                "display_symbol": f"T{index:03d}{offset}",
                "instrument_type": "us_equity_or_fund",
                "identity_evidence": [],
            }
            for offset in range(candidates)
        ],
        "rendered_product": {"text": f"Rendered article {index}"},
    }
    return CollectionItem(sample_id, "teacher", blinded, {})


class SolTeacherPipelineTests(unittest.TestCase):
    def test_calendar_year_selection_is_balanced_and_deterministic(self) -> None:
        rows = [
            _candidate(year, index)
            for year in range(2010, 2027)
            for index in range(20)
        ]
        first = select_teacher_candidates(rows, sample_size=170)
        second = select_teacher_candidates(tuple(reversed(rows)), sample_size=170)
        self.assertEqual(
            [row["source_id"] for row in first],
            [row["source_id"] for row in second],
        )
        counts = {
            year: sum(source_year(row) == year for row in first)
            for year in range(2010, 2027)
        }
        self.assertEqual(set(counts.values()), {10})
        scopes = {
            scope: sum(ticker_scope(row) == scope for row in first)
            for scope in ("zero", "single", "multi")
        }
        self.assertEqual(scopes, {"zero": 34, "single": 85, "multi": 51})

    def test_complete_ground_truth_is_an_exact_exclusion_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtimes" / "news_1000"
            rows = [
                {"sample_id": f"N{index:04d}", "source_id": f"source-{index}"}
                for index in range(1_000)
            ]
            manifest = {
                "sample_version": "sample-v1",
                "sample_count": 1_000,
                "items": rows,
            }
            manifest["sample_manifest_sha256"] = stable_json_hash(manifest)
            write_json_atomic(root / "sample_manifest.json", manifest)
            identifiers, contract = load_ground_truth_exclusion(root)
        self.assertEqual(len(identifiers), 1_000)
        self.assertFalse(contract["overlap_allowed"])
        self.assertEqual(contract["source_count"], 1_000)

    def test_usage_cost_separates_cache_reads_and_writes(self) -> None:
        usage = CounterUsage()
        usage.add(
            {
                "prompt_tokens": 271_476,
                "prompt_tokens_details": {"cache_write_tokens": 2_116},
                "completion_tokens": 23_830,
            }
        )
        usage.add(
            {
                "prompt_tokens": 271_476,
                "prompt_tokens_details": {
                    "cached_tokens": 245_044,
                    "cache_write_tokens": 0,
                },
                "completion_tokens": 27_030,
            }
        )
        self.assertEqual(usage.uncached_input_tokens, 295_792)
        self.assertEqual(usage.cached_input_tokens, 245_044)
        self.assertEqual(usage.cache_write_tokens, 2_116)
        self.assertEqual(usage.cost(), Decimal("1.570253500"))

    def test_plan_partitions_requests_and_reserves_maximum_cost(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtimes" / "teacher"
            config = TeacherBatchConfig(
                corpus_root=root,
                runtime_root=root / "sol_batch",
                chunk_rows=2,
            )
            items = tuple(_collection_item(index) for index in range(5))
            plan = build_teacher_plan(config, items)
        self.assertEqual(plan["chunk_count"], 3)
        self.assertEqual(
            [row["request_rows"] for row in plan["chunks"]], [2, 2, 1]
        )
        self.assertGreater(
            Decimal(plan["maximum_cost_usd"]), Decimal(plan["expected_cost_usd"])
        )


if __name__ == "__main__":
    unittest.main()
