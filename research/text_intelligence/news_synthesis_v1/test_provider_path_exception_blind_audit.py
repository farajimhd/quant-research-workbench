from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .provider_path_exception_blind_audit import (
    resolve_final_label,
    validate_compact_review,
    validate_full_review,
)


class ProviderPathExceptionBlindAuditTests(unittest.TestCase):
    def test_compact_review_requires_exact_identity_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packet = root / "packet.jsonl"
            review = root / "review.jsonl"
            packet.write_text(json.dumps({
                "review_id": "PE1",
                "preview_text": "Title: Company reports earnings",
            }) + "\n", encoding="utf-8")
            review.write_text(json.dumps({
                "review_id": "PE1",
                "manual_label": "eligible",
                "confidence_probability": 0.95,
                "reason_code": "earnings_current",
                "rationale": "Reports current issuer earnings.",
                "evidence_excerpt": "Company reports earnings",
                "isolation_attestation": {
                    "used_only_supplied_packet": True,
                    "used_external_context": False,
                },
            }) + "\n", encoding="utf-8")

            result = validate_compact_review(packet_path=packet, review_path=review)

            self.assertEqual(result["articles"], 1)

    def test_final_label_requires_two_full_reviews_to_change(self) -> None:
        self.assertEqual(
            resolve_final_label(
                current="eligible", full_first="ineligible", full_confirmation="ineligible"
            ),
            ("ineligible", "two_full_reviews_agree_change"),
        )
        self.assertEqual(
            resolve_final_label(
                current="eligible", full_first="ineligible", full_confirmation="eligible"
            ),
            ("eligible", "full_disagreement_fail_closed_preserve"),
        )
        self.assertEqual(
            resolve_final_label(
                current="ineligible", full_first="eligible", full_confirmation=None
            ),
            ("ineligible", "full_disagreement_fail_closed_preserve"),
        )

    def test_full_review_requires_evidence_from_complete_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packet = root / "packet.jsonl"
            review = root / "review.jsonl"
            packet.write_text(json.dumps({
                "review_id": "PE2",
                "rendered_text": "Company entered a definitive merger agreement today.",
            }) + "\n", encoding="utf-8")
            review.write_text(json.dumps({
                "review_id": "PE2",
                "manual_label": "eligible",
                "confidence_probability": 0.99,
                "reason_code": "new_material_event",
                "rationale": "Reports a current definitive merger agreement.",
                "evidence_excerpt": "entered a definitive merger agreement today",
                "isolation_attestation": {
                    "used_only_supplied_packet": True,
                    "used_external_context": False,
                },
            }) + "\n", encoding="utf-8")

            result = validate_full_review(packet_path=packet, review_path=review)

            self.assertEqual(result["articles"], 1)


if __name__ == "__main__":
    unittest.main()
