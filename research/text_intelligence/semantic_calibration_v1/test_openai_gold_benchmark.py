from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from .comparison import CollectionItem, evaluate_predictions
from .openai_gold_benchmark import (
    BenchmarkConfig,
    MODEL_PROFILES,
    output_token_budget,
    prepare_selection,
    quality_score,
    response_schema,
    to_prediction,
    validate_response,
)


def _item(index: int, *, labeled: bool = True) -> CollectionItem:
    ticker = f"T{index:03d}"
    units = [
        {
            "ticker": ticker,
            "event_concepts": ["guidance.raise"],
            "semantic_direction": "positive",
            "forecast_trigger_eligible": True,
            "reaction_evaluation_eligible": True,
            "issuer_history_context_eligible": True,
        }
    ] if labeled else []
    truth = {
        "sample_id": f"N{index:04d}",
        "source_text_sha256": f"{index:064x}",
        "annotation_sha256": f"{index + 100:064x}",
        "extraction_decision": "labeled" if labeled else "non_issuer_market_content",
        "content_role": "primary_event" if labeled else "market_roundup",
        "source_origin": "issuer_direct" if labeled else "editorial_aggregation",
        "issuer_units": units,
    }
    blinded = {
        "source_id": f"source-{index}",
        "publication": {
            "title": f"Article {index}",
            "teaser": "",
            "author": "",
            "provider_tags": [],
            "channels": [],
            "provider_tickers": [ticker] if labeled else [],
        },
        "point_in_time_issuer_candidates": [
            {"ticker": ticker, "identity_evidence": [f"symbol:{ticker}"]}
        ] if labeled else [],
        "rendered_product": {"text": f"Rendered article {index}"},
    }
    return CollectionItem(f"N{index:04d}", "fit", blinded, truth)


class OpenAIGoldBenchmarkTests(unittest.TestCase):
    def test_selection_is_frozen_and_deterministic(self) -> None:
        items = tuple(_item(index, labeled=index % 3 != 0) for index in range(20))
        with tempfile.TemporaryDirectory() as temporary:
            config = BenchmarkConfig(
                collection_root=Path(temporary),
                runtime_root=Path(temporary),
                profiles=("gpt-5.6-luna",),
                sample_size=10,
                hard_max_cost_usd=Decimal("1"),
            )
            first = prepare_selection(config, items)
            second = prepare_selection(config, tuple(reversed(items)))
            self.assertEqual([item.sample_id for item in first], [item.sample_id for item in second])
            self.assertEqual(len(first), 10)

    def test_structured_response_validation_and_projection(self) -> None:
        item = _item(1)
        value = {
            "extraction_decision": "labeled",
            "content_role": "primary_event",
            "source_origin": "issuer_direct",
            "issuer_units": [
                {
                    "ticker": "T001",
                    "semantic_direction": "positive",
                    "event_families": ["guidance"],
                    "forecast_trigger_eligible": True,
                    "reaction_evaluation_eligible": True,
                    "issuer_history_context_eligible": True,
                }
            ],
        }
        self.assertEqual(validate_response(value, item), [])
        prediction = to_prediction(item, value, "gpt-5.6-luna")
        self.assertEqual(prediction["content_role"], "primary_event")
        self.assertEqual(prediction["labels"][0]["ticker"], "T001")

    def test_outside_ticker_is_rejected(self) -> None:
        item = _item(1)
        value = {
            "extraction_decision": "labeled",
            "content_role": "primary_event",
            "source_origin": "issuer_direct",
            "issuer_units": [
                {
                    "ticker": "WRONG",
                    "semantic_direction": "neutral",
                    "event_families": [],
                    "forecast_trigger_eligible": False,
                    "reaction_evaluation_eligible": False,
                    "issuer_history_context_eligible": True,
                }
            ],
        }
        self.assertIn("ticker_outside_candidates:WRONG", validate_response(value, item))

    def test_quality_score_uses_nine_equal_components(self) -> None:
        metrics = {
            "extraction_decision": {"macro_f1": 0.9},
            "ticker_scope": {"f1": 0.8},
            "content_role": {"macro_f1": 0.7},
            "source_origin": {"macro_f1": 0.6},
            "semantic_direction": {"macro_f1": 0.5},
            "event_concepts": {"f1": 0.4},
            "eligibility": {
                "forecast_trigger_eligible": {"f1": 0.3},
                "reaction_evaluation_eligible": {"f1": 0.2},
                "issuer_history_context_eligible": {"f1": 0.1},
            },
        }
        self.assertEqual(quality_score(metrics), 0.5)

    def test_schema_is_strict_and_profiles_are_explicitly_priced(self) -> None:
        self.assertFalse(response_schema()["additionalProperties"])
        self.assertEqual(len(MODEL_PROFILES), 7)
        self.assertEqual(
            MODEL_PROFILES["gpt-5.6-sol"].batch_input_usd_per_million,
            Decimal("2.50"),
        )

    def test_output_budget_expands_for_broad_multi_issuer_article(self) -> None:
        item = _item(999)
        item.blinded["point_in_time_issuer_candidates"] = [
            {"ticker": f"T{index}", "identity_evidence": []}
            for index in range(77)
        ]
        self.assertEqual(
            output_token_budget(item, minimum=2_048, maximum=16_384),
            10_624,
        )

    def test_missing_prediction_is_scored_as_failure_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            metrics = evaluate_predictions(
                (_item(1),),
                prediction_dir=Path(temporary),
                canonical_concepts=True,
                missing_as_failure=True,
            )
        self.assertEqual(metrics["sample_count"], 1)
        self.assertEqual(metrics["extraction"]["false_negative"], 1)
        self.assertEqual(metrics["ticker_scope"]["false_negative"], 1)


if __name__ == "__main__":
    unittest.main()
