from __future__ import annotations

import unittest

from research.text_intelligence.sec_issuer_review_v1.schema import validate_output


class SecIssuerReviewV1Tests(unittest.TestCase):
    def test_rejects_invented_evidence(self) -> None:
        synthesis = {"accession_number": "a", "cik": "1", "narrative_disclosures": [], "reconciliation": []}
        result = {
            "accession_number": "a", "cik": "1", "ticker": "ABC",
            "materiality_probability": 0.8, "forecast_relevance_probability": 0.7,
            "positive_implication_probability": 0.2, "negative_implication_probability": 0.8,
            "fundamental_direction": "negative", "risk_change": "increased", "guidance_change": "none",
            "event_tags": [], "evidence_ids": ["invented"], "conflict_ids": [],
            "abstain": False, "abstention_reasons": [], "summary": "Risk increased.",
        }
        self.assertIn("unknown evidence_ids: ['invented']", validate_output(result, synthesis))


if __name__ == "__main__":
    unittest.main()
