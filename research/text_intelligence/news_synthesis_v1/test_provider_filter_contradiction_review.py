from __future__ import annotations

import unittest

from .provider_filter_contradiction_review import (
    ATTESTATION,
    packetize,
    reconcile_labels,
    validate_review_rows,
)


def review(review_id: str, label: str, reviewer: str = "r") -> dict[str, object]:
    return {
        "review_id": review_id,
        "manual_label": label,
        "confidence_probability": 0.9,
        "reason_code": "material_event" if label == "eligible" else "analyst_action_only",
        "rationale": "The supplied text supports this decision.",
        "evidence_excerpt": "Exact evidence",
        "isolation_attestation": ATTESTATION,
        "reviewer_id": reviewer,
    }


class ProviderFilterContradictionReviewTests(unittest.TestCase):
    def test_packetize_respects_article_and_character_bounds(self) -> None:
        rows = [{"review_id": str(index), "rendered_text": "x" * 2000} for index in range(50)]
        packets = packetize(rows)
        self.assertEqual([len(packet) for packet in packets], [40, 10])
        self.assertLessEqual(sum(len(row["rendered_text"]) for row in packets[0]), 80_000)

    def test_review_validation_requires_exact_evidence_and_order(self) -> None:
        packet = {
            "packet_id": "F0001",
            "articles": [{"review_id": "a", "rendered_text": "Exact evidence in full text."}],
        }
        row = review("a", "eligible")
        row.pop("reviewer_id")
        validated = validate_review_rows(packet, [row])
        self.assertEqual(validated[0]["manual_label"], "eligible")
        row["evidence_excerpt"] = "not present"
        with self.assertRaisesRegex(ValueError, "evidence"):
            validate_review_rows(packet, [row])

    def test_reconcile_uses_majority_and_preserves_three_way_conflict(self) -> None:
        first = {"a": review("a", "ineligible", "r1"), "b": review("b", "ineligible", "r1")}
        second = {"a": review("a", "eligible", "r2"), "b": review("b", "eligible", "r2")}
        third = {"a": review("a", "ineligible", "r3"), "b": review("b", "insufficient_information", "r3")}
        final, unresolved = reconcile_labels(first, second, third)
        self.assertEqual(final["a"], "ineligible")
        self.assertEqual(final["b"], "eligible")
        self.assertEqual(unresolved, {"b"})


if __name__ == "__main__":
    unittest.main()
