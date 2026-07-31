from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from base64 import b64encode
from pathlib import Path

from .annotation_template import annotation_template
from .review_round import (
    is_analyst_related_unit,
    normalize_maintained_rating_endpoints,
    upgrade_v1_annotation,
)
from .run_record_annotation import finalize_staged, stage_chunk
from .sampling import distribute_quota, select_candidates
from .schema import ANNOTATION_VERSION, ANNOTATION_VERSION_V1, validate_annotation
from .storage import (
    annotation_directory,
    assert_runtime_root,
    materialize_evidence_spans,
    read_json,
    write_json_atomic,
)


class SemanticCalibrationSchemaTests(unittest.TestCase):
    def test_complete_positive_annotation_is_valid(self) -> None:
        item = {
            "sample_id": "N0001",
            "source_id": "source",
            "source_timestamp": "2026-01-01 12:00:00.000000000",
            "source_text_sha256": "a" * 64,
        }
        value = annotation_template(item)
        value["issuer_units"][0].update(
            {
                "ticker": "AAPL",
                "event_concepts": ["guidance.raise"],
                "evidence_quotes": ["raised guidance"],
                "evidence_spans": [
                    {
                        "source_field": "rendered_text",
                        "start": 0,
                        "end": 15,
                        "quote": "raised guidance",
                    }
                ],
                "positive_evidence_level": 3,
                "semantic_direction": "positive",
                "forecast_trigger_eligible": True,
                "reaction_evaluation_eligible": True,
                "annotation_confidence": 4,
                "semantic_rationale": "Forward guidance improved materially.",
            }
        )
        value["reviewer_confidence"] = 4
        result = validate_annotation(value, expected_item=item)
        self.assertTrue(result.valid, result.errors)

    def test_negative_confidence_is_rejected(self) -> None:
        item = {
            "sample_id": "N0001",
            "source_id": "source",
            "source_timestamp": "2026-01-01 12:00:00.000000000",
            "source_text_sha256": "a" * 64,
        }
        value = annotation_template(item)
        value["reviewer_confidence"] = -1
        result = validate_annotation(value, expected_item=item)
        self.assertIn("reviewer_confidence_must_be_0_to_4", result.errors)

    def test_v2_analyst_opinion_uses_separate_rating_and_target_fields(self) -> None:
        item = {
            "sample_id": "N0001",
            "source_id": "source",
            "source_timestamp": "2026-01-01 12:00:00.000000000",
            "source_text_sha256": "a" * 64,
        }
        value = annotation_template(item)
        unit = value["issuer_units"][0]
        unit.update(
            {
                "ticker": "AAPL",
                "issuer_role": "analyst_subject",
                "modality": "opinion",
                "event_concepts": ["analyst.price_target_raise"],
                "evidence_quotes": ["maintains Overweight and raises the price target from $360 to $364"],
                "evidence_spans": [
                    {
                        "source_field": "rendered_text",
                        "start": 0,
                        "end": 67,
                        "quote": "maintains Overweight and raises the price target from $360 to $364",
                    }
                ],
                "positive_evidence_level": 1,
                "semantic_direction": "positive",
                "semantic_rationale": "The stated target increased while the rating was maintained.",
                "analyst_context_eligible": True,
                "analyst_evaluation_eligible": True,
                "analyst_opinions": [
                    {
                        "opinion_kind": "individual",
                        "analyst_name": "Erik Woodring",
                        "analyst_aliases": [],
                        "firm_name": "Morgan Stanley",
                        "firm_aliases": [],
                        "employment_valid_from": None,
                        "employment_valid_to": None,
                        "rating_action": "maintained",
                        "rating_from": "Overweight",
                        "rating_to": "Overweight",
                        "price_target_action": "raised",
                        "price_target_from": 360,
                        "price_target_to": 364,
                        "price_target_currency": "USD",
                        "forecast_horizon_text": None,
                        "reasoning_not_provided": True,
                        "reasoning_quotes": [],
                        "evidence_quotes": [
                            "maintains Overweight and raises the price target from $360 to $364"
                        ],
                        "evidence_spans": [
                            {
                                "source_field": "rendered_text",
                                "start": 0,
                                "end": 67,
                                "quote": "maintains Overweight and raises the price target from $360 to $364",
                            }
                        ],
                        "annotation_confidence": 4,
                        "ambiguity_notes": "",
                    }
                ],
                "eligibility_reason": "Analyst context only.",
                "annotation_confidence": 4,
            }
        )
        value["content_role"] = "analyst_event"
        value["source_origin"] = "analyst_research"
        value["reviewer_confidence"] = 4
        result = validate_annotation(value, expected_item=item)
        self.assertTrue(result.valid, result.errors)

        value["issuer_units"][0]["analyst_opinions"][0]["rating_from"] = None
        result = validate_annotation(value, expected_item=item)
        self.assertIn(
            "issuer_units[0].analyst_opinions[0].rating_maintained_requires_from_and_to",
            result.errors,
        )

        value["issuer_units"][0]["analyst_opinions"][0]["rating_from"] = "Neutral"
        result = validate_annotation(value, expected_item=item)
        self.assertIn(
            "issuer_units[0].analyst_opinions[0].rating_maintained_requires_equal_endpoints",
            result.errors,
        )

    def test_v2_analyst_context_cannot_be_primary_reaction_trigger(self) -> None:
        item = {
            "sample_id": "N0001",
            "source_id": "source",
            "source_timestamp": "2026-01-01 12:00:00.000000000",
            "source_text_sha256": "a" * 64,
        }
        value = annotation_template(item)
        unit = value["issuer_units"][0]
        unit.update(
            {
                "ticker": "AAPL",
                "event_concepts": ["analyst.rating_maintained"],
                "evidence_quotes": ["maintains Overweight"],
                "evidence_spans": [
                    {
                        "source_field": "title",
                        "start": 0,
                        "end": 20,
                        "quote": "maintains Overweight",
                    }
                ],
                "semantic_rationale": "Analyst rating statement.",
                "analyst_context_eligible": True,
                "forecast_trigger_eligible": True,
            }
        )
        result = validate_annotation(value, expected_item=item)
        self.assertIn(
            "issuer_units[0].analyst_context_cannot_be_forecast_trigger",
            result.errors,
        )

    def test_round_two_upgrade_preserves_v1_and_marks_analyst_review(self) -> None:
        annotation = {
            "annotation_version": ANNOTATION_VERSION_V1,
            "annotation_sha256": "immutable-v1",
            "review_round": 1,
            "content_role": "analyst_event",
            "issuer_units": [
                {
                    "ticker": "AAPL",
                    "issuer_role": "analyst_subject",
                    "event_concepts": ["analyst.price_target_raise"],
                    "forecast_trigger_eligible": True,
                    "reaction_evaluation_eligible": True,
                    "eligibility_reason": "Old pilot decision.",
                }
            ],
        }
        upgraded, review_required = upgrade_v1_annotation(annotation)
        self.assertTrue(review_required)
        self.assertEqual(upgraded["annotation_version"], ANNOTATION_VERSION)
        self.assertEqual(upgraded["review_round"], 2)
        self.assertNotIn("annotation_sha256", upgraded)
        self.assertFalse(upgraded["issuer_units"][0]["forecast_trigger_eligible"])
        self.assertFalse(upgraded["issuer_units"][0]["reaction_evaluation_eligible"])
        self.assertTrue(upgraded["issuer_units"][0]["analyst_context_eligible"])
        self.assertEqual(annotation["annotation_sha256"], "immutable-v1")

    def test_versioned_annotation_directories_are_separate(self) -> None:
        root = Path("D:/TradingML/runtimes/test")
        self.assertEqual(annotation_directory(root, ANNOTATION_VERSION_V1), root / "annotations")
        self.assertEqual(annotation_directory(root, ANNOTATION_VERSION), root / "annotations_v2")

    def test_rating_endpoint_normalizer_records_traceable_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtimes" / "semantic_calibration"
            item = {
                "sample_id": "N0001",
                "source_id": "source",
                "source_timestamp": "2026-01-01 12:00:00.000000000",
                "source_text_sha256": "a" * 64,
            }
            (root / "blinded_articles").mkdir(parents=True)
            write_json_atomic(root / "blinded_articles" / "N0001.json", item)
            write_json_atomic(
                root / "sample_manifest.json",
                {
                    "sample_version": "test",
                    "sample_manifest_sha256": "b" * 64,
                    "items": [{"sample_id": "N0001"}],
                },
            )
            value = annotation_template(item)
            value["content_role"] = "analyst_event"
            value["source_origin"] = "analyst_research"
            value["reviewer_confidence"] = 4
            value["issuer_units"][0].update(
                {
                    "ticker": "AAPL",
                    "issuer_role": "analyst_subject",
                    "modality": "opinion",
                    "event_concepts": ["analyst.rating_maintained"],
                    "evidence_quotes": ["maintains Overweight"],
                    "evidence_spans": [
                        {
                            "source_field": "title",
                            "start": 0,
                            "end": 20,
                            "quote": "maintains Overweight",
                        }
                    ],
                    "semantic_rationale": "The analyst maintained the stated rating.",
                    "analyst_context_eligible": True,
                    "analyst_evaluation_eligible": True,
                    "issuer_history_context_eligible": True,
                    "eligibility_reason": "Analyst context only.",
                    "annotation_confidence": 4,
                    "analyst_opinions": [
                        {
                            "opinion_kind": "firm",
                            "analyst_name": None,
                            "analyst_aliases": [],
                            "firm_name": "Example Research",
                            "firm_aliases": [],
                            "employment_valid_from": None,
                            "employment_valid_to": None,
                            "rating_action": "maintained",
                            "rating_from": None,
                            "rating_to": "Overweight",
                            "price_target_action": "not_stated",
                            "price_target_from": None,
                            "price_target_to": None,
                            "price_target_currency": None,
                            "forecast_horizon_text": None,
                            "reasoning_not_provided": True,
                            "reasoning_quotes": [],
                            "evidence_quotes": ["maintains Overweight"],
                            "evidence_spans": [
                                {
                                    "source_field": "title",
                                    "start": 0,
                                    "end": 20,
                                    "quote": "maintains Overweight",
                                }
                            ],
                            "annotation_confidence": 4,
                            "ambiguity_notes": "",
                        }
                    ],
                }
            )
            value["annotation_sha256"] = "old"
            write_json_atomic(root / "annotations_v2" / "N0001.json", value)

            manifest = normalize_maintained_rating_endpoints(root)
            revised = read_json(root / "annotations_v2" / "N0001.json")
            self.assertEqual(
                revised["issuer_units"][0]["analyst_opinions"][0]["rating_from"],
                "Overweight",
            )
            self.assertEqual(manifest["changed_records"], 1)
            self.assertEqual(manifest["changes"][0]["old_annotation_sha256"], "old")

    def test_chunked_manual_annotation_transport_is_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtimes" / "semantic_calibration"
            item = {
                "sample_id": "N0001",
                "source_id": "source",
                "source_timestamp": "2026-01-01 12:00:00.000000000",
                "source_text_sha256": "a" * 64,
                "publication": {"title": "Issuer raised guidance", "teaser": ""},
                "rendered_product": {"text": ""},
                "source_lanes": [],
            }
            write_json_atomic(root / "blinded_articles" / "N0001.json", item)
            write_json_atomic(
                root / "sample_manifest.json",
                {
                    "sample_version": "test",
                    "sample_manifest_sha256": "b" * 64,
                    "items": [{"sample_id": "N0001"}],
                },
            )
            value = annotation_template(item)
            value["reviewer_confidence"] = 4
            value["issuer_units"][0].update(
                {
                    "ticker": "TEST",
                    "event_concepts": ["guidance.raise"],
                    "evidence_quotes": ["raised guidance"],
                    "semantic_direction": "positive",
                    "positive_evidence_level": 3,
                    "forecast_trigger_eligible": True,
                    "reaction_evaluation_eligible": True,
                    "annotation_confidence": 4,
                    "semantic_rationale": "Raised guidance is positive.",
                }
            )
            payload = json.dumps(value).encode("utf-8")
            split = len(payload) // 2
            for index, part in enumerate((payload[:split], payload[split:])):
                args = Namespace(
                    runtime_root=root,
                    stage_sample="N0001",
                    stage_index=index,
                    stage_total=2,
                    stage_base64=b64encode(part).decode("ascii"),
                )
                self.assertEqual(stage_chunk(args), 0)
                self.assertEqual(stage_chunk(args), 0)
            self.assertEqual(finalize_staged(root, "N0001"), 0)
            self.assertTrue((root / "annotations_v2" / "N0001.json").exists())
            self.assertFalse((root / "annotation_staging_v2" / "N0001").exists())

    def test_raw_stdin_payload_uses_the_same_staging_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = Namespace(
                runtime_root=root,
                stage_sample="N0001",
                stage_index=0,
                stage_total=1,
                stage_base64=None,
            )
            self.assertEqual(stage_chunk(args, payload=b'{"sample_id":"N0001"}'), 0)
            chunk = read_json(root / "annotation_staging_v2" / "N0001" / "00000.json")
            self.assertEqual(
                chunk["payload_base64"],
                b64encode(b'{"sample_id":"N0001"}').decode("ascii"),
            )

    def test_analyst_related_detection_covers_embedded_concepts(self) -> None:
        self.assertTrue(
            is_analyst_related_unit(
                {
                    "issuer_role": "primary_subject",
                    "event_concepts": ["analyst.price_target_raise"],
                },
                "editorial_analysis",
            )
        )
        self.assertFalse(
            is_analyst_related_unit(
                {
                    "issuer_role": "primary_subject",
                    "event_concepts": ["guidance.operating_income_growth"],
                },
                "market_roundup",
            )
        )

    def test_runtime_root_rejects_repository(self) -> None:
        repository = Path(__file__).resolve().parents[3]
        with self.assertRaises(ValueError):
            assert_runtime_root(repository / "runtime")

    def test_quotes_are_materialized_to_exact_spans(self) -> None:
        annotation = {
            "issuer_units": [
                {
                    "ticker": "AAPL",
                    "evidence_quotes": ["raised guidance"],
                    "evidence_spans": [],
                }
            ]
        }
        item = {
            "publication": {"title": "Issuer raised guidance", "teaser": ""},
            "rendered_product": {"text": "Title: Issuer raised guidance"},
            "source_lanes": [],
        }
        result = materialize_evidence_spans(annotation, item)
        self.assertEqual(
            result["issuer_units"][0]["evidence_spans"],
            [
                {
                    "source_field": "title",
                    "start": 7,
                    "end": 22,
                    "quote": "raised guidance",
                }
            ],
        )

    def test_analyst_spans_materialize_when_unit_spans_already_exist(self) -> None:
        annotation = {
            "issuer_units": [
                {
                    "ticker": "AAPL",
                    "evidence_quotes": ["maintains Overweight"],
                    "evidence_spans": [
                        {
                            "source_field": "title",
                            "start": 7,
                            "end": 27,
                            "quote": "maintains Overweight",
                        }
                    ],
                    "analyst_opinions": [
                        {
                            "evidence_quotes": ["raises the target"],
                            "reasoning_quotes": [],
                            "evidence_spans": [],
                        }
                    ],
                }
            ]
        }
        item = {
            "publication": {
                "title": "Issuer maintains Overweight and raises the target",
                "teaser": "",
            },
            "rendered_product": {"text": ""},
            "source_lanes": [],
        }
        result = materialize_evidence_spans(annotation, item)
        self.assertEqual(
            result["issuer_units"][0]["analyst_opinions"][0]["evidence_spans"][0][
                "quote"
            ],
            "raises the target",
        )

    def test_era_balancing_is_exact(self) -> None:
        rows = []
        for era_index, year in enumerate((2011, 2016, 2021, 2025)):
            for index in range(300):
                rows.append(
                    {
                        "source_id": f"{era_index}-{index}",
                        "source_timestamp": f"{year}-01-01 12:00:00",
                        "event": {"tickers": ["TST"]},
                        "v5_units": [
                            {
                                "content_role": "primary_event",
                                "semantic_direction": "positive",
                                "event_concepts": [f"concept.{index % 20}"],
                            }
                        ],
                    }
                )
        selected = select_candidates(rows, sample_size=1_000, rare_supplement=100)
        counts = {}
        for row in selected:
            year = int(row["source_timestamp"][:4])
            key = 2010 if year < 2015 else 2015 if year < 2020 else 2020 if year < 2024 else 2024
            counts[key] = counts.get(key, 0) + 1
        self.assertEqual(counts, {2010: 250, 2015: 250, 2020: 250, 2024: 250})
        self.assertEqual(distribute_quota(10, ("a", "b", "c")), {"a": 4, "b": 3, "c": 3})


if __name__ == "__main__":
    unittest.main()
