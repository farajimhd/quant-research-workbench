from __future__ import annotations

import unittest

from scripts.prepare_news_v59_file_level_reaudit import (
    ADJUDICATION_REVIEW_FIELDS,
    BODY_REVIEW_FIELDS,
    PACKET_FILE_LIMIT,
    PACKET_ROW_LIMIT,
    REVIEW_FIELDS,
    adjudication_lane,
    effective_row_review,
    packetize_groups,
    packetize_body_rows,
    validate_adjudication_row,
    validate_body_review_row,
    validate_review_row,
)


class NewsV59FileLevelReauditTest(unittest.TestCase):
    def test_body_packetizer_bounds_rows_and_characters(self) -> None:
        rows = [
            {"review_id": f"R{index}", "article_text": "x" * size}
            for index, size in enumerate((40_000, 40_000, 1, 50_000))
        ]
        packets = packetize_body_rows(rows)
        self.assertEqual([len(packet) for packet in packets], [2, 2])
        self.assertEqual(
            [row["review_id"] for packet in packets for row in packet],
            [row["review_id"] for row in rows],
        )

    def test_body_review_schema_requires_insufficient_uncertainty(self) -> None:
        row = {
            "review_id": "R1",
            "label": "eligible",
            "confidence": "high",
            "policy_id": "issuer_guidance",
            "decisive_evidence": "issuer raises guidance",
            "source_sufficient": True,
            "discovered_pattern": "issuer raises guidance",
            "justification": "The body confirms direct issuer guidance.",
        }
        self.assertEqual(set(row), BODY_REVIEW_FIELDS)
        validate_body_review_row(row, "R1", "packet")
        row["label"] = "uncertain"
        with self.assertRaisesRegex(ValueError, "source-sufficient"):
            validate_body_review_row(row, "R1", "packet")

    def test_adjudication_lane_avoids_known_pass_two_reviewers(self) -> None:
        self.assertEqual(adjudication_lane("G2-1-043"), "lane_a")
        self.assertEqual(adjudication_lane("G2-2-042"), "lane_b")
        self.assertEqual(adjudication_lane("G2-2-043"), "lane_a")
        self.assertEqual(adjudication_lane("G2-3-048"), "lane_c")

    def test_adjudication_review_schema_requires_body_for_uncertain(self) -> None:
        row = {
            "review_id": "R1",
            "label": "ineligible",
            "confidence": "high",
            "policy_id": "price_reaction_wrapper",
            "decisive_evidence": {"title_evidence": ["shares rise", "here is why"]},
            "needs_article_body": False,
            "discovered_pattern": "X shares rise: here is why",
            "justification": "The article is presented as a reaction wrapper.",
        }
        self.assertEqual(set(row), ADJUDICATION_REVIEW_FIELDS)
        validate_adjudication_row(row, "R1", "packet")
        row["label"] = "uncertain"
        with self.assertRaisesRegex(ValueError, "without body request"):
            validate_adjudication_row(row, "R1", "packet")

    def test_effective_row_review_applies_exception_and_body_flag(self) -> None:
        file_review = {
            "label": "ineligible",
            "policy_id": "price_reaction_wrapper",
            "justification": "dominant wrapper policy",
            "exceptions": [
                {
                    "review_id": "R2",
                    "label": "eligible",
                    "policy_id": "issuer_guidance",
                    "reason": "explicit issuer guidance",
                    "needs_article_body": True,
                }
            ],
            "needs_article_body_review_ids": ["R2"],
        }
        self.assertEqual(
            effective_row_review(file_review, "R1"),
            {
                "label": "ineligible",
                "policy_id": "price_reaction_wrapper",
                "reason": "dominant wrapper policy",
                "needs_article_body": False,
            },
        )
        self.assertEqual(effective_row_review(file_review, "R2")["label"], "eligible")
        self.assertTrue(effective_row_review(file_review, "R2")["needs_article_body"])

    def test_packetizer_bounds_files_and_rows_without_dropping_groups(self) -> None:
        groups = [
            {"file_id": f"F{index}", "rows": rows}
            for index, rows in enumerate((100, 100, 100, 100, 1, 99, 100, 100, 100))
        ]
        packets = packetize_groups(groups)

        self.assertEqual(
            [group["file_id"] for packet in packets for group in packet],
            [group["file_id"] for group in groups],
        )
        self.assertTrue(all(len(packet) <= PACKET_FILE_LIMIT for packet in packets))
        self.assertTrue(
            all(sum(int(group["rows"]) for group in packet) <= PACKET_ROW_LIMIT for packet in packets)
        )

    def test_file_review_schema_validates_exception_membership(self) -> None:
        group = {"file_id": "F59001", "review_ids": ["R1", "R2"]}
        row = {
            "file_id": "F59001",
            "label": "ineligible",
            "confidence": "high",
            "policy_id": "price_reaction_wrapper",
            "pattern_verdict": "dominant pattern is ineligible",
            "decisive_evidence": "shares jump: here is why",
            "exceptions": [
                {
                    "review_id": "R2",
                    "label": "eligible",
                    "policy_id": "earnings_call_transcript",
                    "reason": "complete transcript exception",
                    "needs_article_body": False,
                }
            ],
            "needs_article_body_review_ids": [],
            "discovered_subpatterns": ["X shares jump: here is why"],
            "justification": "The recurring price-reaction wrapper is not a new issuer event.",
        }
        self.assertEqual(set(row), REVIEW_FIELDS)
        validate_review_row(row, group, "test")

        row["exceptions"][0]["review_id"] = "UNKNOWN"
        with self.assertRaisesRegex(ValueError, "invalid exception identity"):
            validate_review_row(row, group, "test")

    def test_file_review_schema_validates_exception_semantics(self) -> None:
        group = {"file_id": "F59001", "review_ids": ["R1", "R2"]}
        row = {
            "file_id": "F59001",
            "label": "ineligible",
            "confidence": "medium",
            "policy_id": "mixed_title_family",
            "pattern_verdict": "mostly ineligible with one unresolved exception",
            "decisive_evidence": ["recurring price-reaction wrapper"],
            "exceptions": [
                {
                    "review_id": "R2",
                    "label": "uncertain",
                    "policy_id": "third_party_material_event",
                    "reason": "title requires article-body confirmation",
                    "needs_article_body": True,
                }
            ],
            "needs_article_body_review_ids": ["R2"],
            "discovered_subpatterns": ["X shares rise: what is going on"],
            "justification": "The wrapper dominates, while one attributed event is unresolved.",
        }
        validate_review_row(row, group, "test")

        row["decisive_evidence"] = {
            "title_evidence": ["shares jump", "here is why"],
            "metadata_evidence": "single issuer channel does not override the wrapper",
        }
        validate_review_row(row, group, "test")

        row["needs_article_body_review_ids"] = []
        with self.assertRaisesRegex(ValueError, "parity mismatch"):
            validate_review_row(row, group, "test")

if __name__ == "__main__":
    unittest.main()
