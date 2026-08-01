from __future__ import annotations

import copy
import unittest

from .annotation_template import annotation_template
from .coverage_review_v3 import build_review_package, finalize_v3_annotation
from .schema import (
    ANNOTATION_VERSION,
    ANNOTATION_VERSION_V3,
    stable_json_hash,
    validate_annotation,
)


class CoverageReviewV3Tests(unittest.TestCase):
    def test_inventory_unions_supplied_explicit_and_existing_tickers(self) -> None:
        item = _item()
        annotation = _annotation(item)
        package = build_review_package(item, annotation)
        self.assertEqual(package["candidate_tickers"], ["AAA", "BBB"])
        self.assertEqual(package["manual_review_tickers"], ["BBB"])

    def test_finalize_requires_disposition_for_every_candidate(self) -> None:
        item = _item()
        annotation = _annotation(item)
        with self.assertRaisesRegex(ValueError, "coverage decisions mismatch"):
            finalize_v3_annotation(
                item,
                annotation,
                {"reviewer": "codex_primary", "ticker_dispositions": []},
            )

    def test_finalize_materializes_exhaustive_v3_annotation(self) -> None:
        item = _item()
        annotation = _annotation(item)
        record = finalize_v3_annotation(
            item,
            annotation,
            {
                "reviewer": "codex_primary",
                "review_notes": "Every candidate was reviewed.",
                "ticker_dispositions": [
                    {
                        "ticker": "AAA",
                        "disposition": "labeled_issuer_unit",
                        "annotation_confidence": 4,
                        "rationale": "Existing supported issuer unit.",
                        "evidence_quotes": [
                            "AAA Corp. (NASDAQ:AAA) reported results."
                        ],
                        "evidence_spans": [],
                    },
                    {
                        "ticker": "BBB",
                        "disposition": "analyst_context",
                        "annotation_confidence": 4,
                        "rationale": "Analyst context is present but is not a primary event.",
                        "evidence_quotes": [
                            "Analysts upgraded BBB Corp. (NYSE:BBB)."
                        ],
                        "evidence_spans": [],
                    },
                ],
                "added_issuer_units": [],
            },
        )
        self.assertEqual(record["annotation_version"], ANNOTATION_VERSION_V3)
        self.assertEqual(record["issuer_unit_coverage"], "exhaustive")
        self.assertTrue(validate_annotation(record, expected_item=item).valid)

    def test_finalize_replaces_exact_hash_bound_unit(self) -> None:
        item = _item()
        annotation = _annotation(item)
        replacement = copy.deepcopy(annotation["issuer_units"][0])
        replacement["forecast_trigger_eligible"] = False
        replacement["reaction_evaluation_eligible"] = False
        replacement["eligibility_reason"] = "Aggregation context, not a new trigger."
        record = finalize_v3_annotation(
            item,
            annotation,
            _decisions(
                annotation,
                replaced=[
                    {
                        "source_unit_index": 0,
                        "source_unit_sha256": stable_json_hash(annotation["issuer_units"][0]),
                        "rationale": "Correct trigger eligibility.",
                        "replacement_unit": replacement,
                    }
                ],
            ),
        )
        self.assertFalse(record["issuer_units"][0]["forecast_trigger_eligible"])
        self.assertTrue(validate_annotation(record, expected_item=item).valid)

    def test_finalize_removes_exact_hash_bound_unit(self) -> None:
        item = _item()
        annotation = _annotation(item)
        record = finalize_v3_annotation(
            item,
            annotation,
            _decisions(
                annotation,
                removed=[
                    {
                        "source_unit_index": 0,
                        "source_unit_sha256": stable_json_hash(annotation["issuer_units"][0]),
                        "rationale": "Ticker mention was incidental.",
                    }
                ],
            ),
        )
        self.assertEqual(record["issuer_units"], [])
        self.assertEqual(record["extraction_decision"], "no_supported_event")
        self.assertTrue(validate_annotation(record, expected_item=item).valid)

    def test_finalize_rejects_correction_after_source_drift(self) -> None:
        item = _item()
        annotation = _annotation(item)
        with self.assertRaisesRegex(ValueError, "source drift"):
            finalize_v3_annotation(
                item,
                annotation,
                _decisions(
                    annotation,
                    removed=[
                        {
                            "source_unit_index": 0,
                            "source_unit_sha256": "0" * 64,
                            "rationale": "Invalid stale correction.",
                        }
                    ],
                ),
            )


def _item() -> dict:
    return {
        "sample_id": "NTEST",
        "source_id": "source",
        "source_timestamp": "2026-01-02 12:00:00.000000000",
        "source_text_sha256": "a" * 64,
        "publication": {
            "title": "AAA reports results",
            "teaser": "",
            "provider_tickers": ["AAA", "BBB"],
        },
        "rendered_product": {
            "text": "AAA Corp. (NASDAQ:AAA) reported results. Analysts upgraded BBB Corp. (NYSE:BBB).",
        },
        "point_in_time_issuer_candidates": [],
        "source_lanes": [],
    }


def _annotation(item: dict) -> dict:
    value = annotation_template(item)
    value["annotation_version"] = ANNOTATION_VERSION
    value["extraction_decision"] = "labeled"
    value["content_role"] = "primary_event"
    value["source_origin"] = "issuer_direct"
    value["issuer_units"] = [
        {
            "ticker": "AAA",
            "issuer_role": "primary_subject",
            "evidence_scope": "ticker_specific",
            "event_concepts": ["earnings_results"],
            "evidence_quotes": ["AAA Corp. (NASDAQ:AAA) reported results."],
            "evidence_spans": [{"source_field": "rendered_text", "start": 0, "end": 42, "quote": "AAA Corp. (NASDAQ:AAA) reported results."}],
            "modality": "confirmed",
            "time_orientation": "current",
            "positive_evidence_level": 0,
            "negative_evidence_level": 0,
            "semantic_direction": "neutral",
            "forecast_trigger_eligible": True,
            "reaction_evaluation_eligible": True,
            "issuer_history_context_eligible": True,
            "eligibility_reason": "Direct event.",
            "annotation_confidence": 4,
            "ambiguity_notes": "",
            "semantic_rationale": "Results reported without direction.",
            "analyst_context_eligible": False,
            "analyst_evaluation_eligible": False,
            "analyst_opinions": [],
        }
    ]
    value["reviewer_confidence"] = 4
    return copy.deepcopy(value)


def _decisions(
    annotation: dict,
    *,
    replaced: list[dict] | None = None,
    removed: list[dict] | None = None,
) -> dict:
    return {
        "reviewer": "codex_primary",
        "review_notes": "Every candidate and existing unit was reviewed.",
        "ticker_dispositions": [
            {
                "ticker": "AAA",
                "disposition": (
                    "incidental_context"
                    if any(int(value["source_unit_index"]) == 0 for value in (removed or []))
                    else "labeled_issuer_unit"
                ),
                "annotation_confidence": 4,
                "rationale": "Existing supported issuer unit.",
                "evidence_quotes": [],
                "evidence_spans": [],
            },
            {
                "ticker": "BBB",
                "disposition": "analyst_context",
                "annotation_confidence": 4,
                "rationale": "Reviewed analyst context.",
                "evidence_quotes": [],
                "evidence_spans": [],
            },
        ],
        "added_issuer_units": [],
        "replaced_issuer_units": replaced or [],
        "removed_issuer_units": removed or [],
    }


if __name__ == "__main__":
    unittest.main()
