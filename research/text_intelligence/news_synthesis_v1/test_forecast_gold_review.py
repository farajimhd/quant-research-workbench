from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from .forecast_gold_review import (
    _bounded_batches,
    _sha256_json,
    certify_consensus,
    evaluate_article_forecast_eligibility,
    prepare_adjudication,
    prepare_full_source_review,
    validate_certified_authority,
    validate_reviews,
)


class ForecastGoldReviewTest(unittest.TestCase):
    def test_prepare_can_preserve_unresolved_high_recall_candidates(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sampling = root / "sampling"
            sampling.mkdir()
            self._write_jsonl(sampling / "silver_screening_labels.jsonl", [
                {"source_id": "eligible-source", "screening_label": "eligible"},
                {"source_id": "unresolved-source", "screening_label": "unresolved"},
                {"source_id": "ineligible-source", "screening_label": "ineligible"},
            ])
            articles = []
            for source_id in ("eligible-source", "unresolved-source", "ineligible-source"):
                articles.append({
                    "source_id": source_id,
                    "source_timestamp": "2026-01-01T12:00:00+00:00",
                    "tickers": ["AAA"],
                    "title": source_id,
                    "text": f"{source_id} body",
                })
            self._write_jsonl(sampling / "sampled_articles.jsonl", articles)
            (sampling / "completion_manifest.json").write_text("{}\n", encoding="utf-8")

            output = root / "gold"
            manifest = prepare_full_source_review(
                sampling,
                output,
                screening_labels=("eligible", "unresolved"),
            )

            self.assertEqual(manifest["population"]["articles"], 2)
            self.assertEqual(
                manifest["review"]["selected_screening_labels"],
                ["eligible", "unresolved"],
            )
            rows = [json.loads(line) for line in (output / "review_answer_key.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 2)

    def test_batches_bound_articles_and_body_chars(self) -> None:
        rows = [
            {"review_id": f"G{index}", "provider_tickers": ["T"], "full_rendered_body": "x" * size}
            for index, size in enumerate((10, 20, 40, 100))
        ]
        batches = _bounded_batches(rows, max_articles=2, max_body_chars=50)
        self.assertEqual([len(batch) for batch in batches], [2, 1, 1])

    def test_review_validation_requires_all_tickers_and_exact_evidence(self) -> None:
        inputs = {
            "G1": {
                "review_id": "G1",
                "provider_tickers": ["AAA"],
                "title": "AAA reports results",
                "full_rendered_body": "AAA revenue increased 20%.",
            }
        }
        review = {
            "review_id": "G1",
            "issuer_units": [{
                "ticker": "AAA",
                "identity_status": "resolved_focal_issuer",
                "forecast_eligibility": "eligible",
                "sentiment": "positive",
                "evidence": [{"source_field": "full_rendered_body", "quote": "AAA revenue increased 20%."}],
                "reason_codes": ["current_material_issuer_event", "directional_economic_implication"],
                "reason": "Current positive issuer results.",
            }],
            "article_reason": "Current issuer earnings report.",
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "review.jsonl"
            path.write_text(json.dumps(review) + "\n", encoding="utf-8")
            self.assertEqual(set(validate_reviews([path], inputs)), {"G1"})
            review["issuer_units"][0]["evidence"][0]["quote"] = "not in source"
            path.write_text(json.dumps(review) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "quote not found"):
                validate_reviews([path], inputs)

    def test_disagreement_gets_blind_third_review_and_recorded_manual_adjudication(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_root = root / "blind_full_source_batches"
            input_root.mkdir()
            inputs = [
                self._input("G1", "AAA", "AAA raises guidance"),
                self._input("G2", "BBB", "BBB issues an update"),
            ]
            self._write_jsonl(input_root / "batch_001.jsonl", inputs)
            self._write_jsonl(root / "review_answer_key.jsonl", [
                self._answer("G1", "source-1"),
                self._answer("G2", "source-2"),
            ])
            first = root / "first.jsonl"
            second = root / "second.jsonl"
            self._write_jsonl(first, [
                self._review("G1", "AAA", "eligible", "positive", "AAA raises guidance"),
                self._review("G2", "BBB", "eligible", "positive", "BBB issues an update"),
            ])
            self._write_jsonl(second, [
                self._review("G1", "AAA", "eligible", "positive", "AAA raises guidance"),
                self._review("G2", "BBB", "ineligible", "not_applicable", "BBB issues an update"),
            ])

            adjudication = prepare_adjudication(root, [first], [second])
            self.assertEqual(adjudication["articles_requiring_third_review"], 1)
            third = root / "third.jsonl"
            self._write_jsonl(third, [
                self._review("G2", "BBB", "eligible", "mixed", "BBB issues an update"),
            ])
            manual = root / "manual.jsonl"
            manual_row = self._review("G2", "BBB", "ineligible", "not_applicable", "BBB issues an update")
            manual_row["source_id"] = "source-2"
            self._write_jsonl(manual, [manual_row])
            manifest = certify_consensus(root, [third], [manual])

            self.assertEqual(manifest["population"]["certified_articles"], 2)
            self.assertEqual(manifest["population"]["policy_uncertain_articles"], 0)
            self.assertEqual(manifest["population"]["certified_issuer_units"], 2)
            self.assertEqual(manifest["population"]["manual_adjudicated_units"], 1)
            self.assertEqual(manifest["article_eligibility_distribution"], {"eligible": 1, "ineligible": 1})
            self.assertEqual(manifest["eligibility_distribution"], {"eligible": 1, "ineligible": 1})
            source_two = json.loads((root / "certified_labels" / "source-2.json").read_text(encoding="utf-8"))
            self.assertEqual(source_two["issuer_units"][0]["forecast_eligibility"], "ineligible")
            self.assertEqual(source_two["issuer_units"][0]["gold_status"], "certified_manual_adjudication")
            validation = validate_certified_authority(root)
            self.assertEqual(validation["status"], "pass")
            self.assertEqual(validation["evidence_spans_verified"], 2)
            self.assertEqual(validation["independent_review_agreement"]["exact_issuer_unit_rate"], 0.5)

            predictions = root / "predictions.jsonl"
            self._write_jsonl(predictions, [
                {
                    "source_id": "source-1",
                    "engine_version": "test-engine",
                    "forecast_eligible_predicted": True,
                    "eligible_entity_ids": ["security:aaa"],
                },
                {
                    "source_id": "source-2",
                    "engine_version": "test-engine",
                    "forecast_eligible_predicted": True,
                    "eligible_entity_ids": ["security:bbb"],
                },
            ])
            evaluation = evaluate_article_forecast_eligibility(root, predictions)
            self.assertEqual(evaluation["confusion_matrix"], {"tp": 1, "fn": 0, "fp": 1, "tn": 0})
            self.assertEqual(evaluation["population"]["scorable_articles"], 2)
            self.assertEqual(evaluation["metrics"]["recall"], 1.0)
            self.assertEqual(evaluation["metrics"]["precision"], 0.5)
            self.assertEqual(evaluation["status"], "pass")

            self._write_jsonl(predictions, [{
                "source_id": "source-1",
                "engine_version": "test-engine",
                "forecast_eligible_predicted": True,
                "eligible_entity_ids": ["security:aaa"],
            }])
            incomplete = evaluate_article_forecast_eligibility(root, predictions)
            self.assertEqual(incomplete["status"], "incomplete")
            self.assertEqual(incomplete["population"]["missing_predictions"], 1)
            self.assertEqual(
                incomplete["authority"]["scored_source_ids_sha256"],
                _sha256_json(["source-1"]),
            )

    @staticmethod
    def _input(review_id: str, ticker: str, title: str) -> dict[str, object]:
        return {
            "review_id": review_id,
            "provider_tickers": [ticker],
            "title": title,
            "full_rendered_body": title,
        }

    @staticmethod
    def _answer(review_id: str, source_id: str) -> dict[str, str]:
        return {
            "review_id": review_id,
            "source_id": source_id,
            "source_timestamp": "2026-01-01T00:00:00Z",
            "title_sha256": "title-hash",
            "body_sha256": "body-hash",
        }

    @staticmethod
    def _review(
        review_id: str,
        ticker: str,
        eligibility: str,
        sentiment: str,
        evidence: str,
    ) -> dict[str, object]:
        return {
            "review_id": review_id,
            "issuer_units": [{
                "ticker": ticker,
                "identity_status": "resolved_focal_issuer",
                "forecast_eligibility": eligibility,
                "sentiment": sentiment,
                "evidence": [{"source_field": "title", "quote": evidence}],
                "reason_codes": [
                    "current_material_issuer_event"
                    if eligibility == "eligible"
                    else "insufficient_directional_evidence"
                ],
                "reason": "Test decision.",
            }],
            "article_reason": "Test article decision.",
        }

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
