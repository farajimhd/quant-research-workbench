from __future__ import annotations

import unittest

from scripts.prepare_news_v59_calibrated_reaudit import (
    CALIBRATION_CASES,
    REVIEW_FIELDS,
    policy_contract,
    validate_review_row,
)


class NewsV59CalibratedReauditTest(unittest.TestCase):
    def test_calibration_is_balanced_and_covers_boundary_policies(self) -> None:
        labels = [label for label, _policy in CALIBRATION_CASES.values()]
        policies = {policy for _label, policy in CALIBRATION_CASES.values()}

        self.assertEqual(labels.count("eligible"), 18)
        self.assertEqual(labels.count("ineligible"), 18)
        self.assertIn("completed_or_priced_financing", policies)
        self.assertIn("atm_shelf_registration", policies)
        self.assertIn("material_ownership", policies)
        self.assertIn("routine_insider_trade", policies)
        self.assertIn("definitive_merger_acquisition", policies)
        self.assertIn("nondefinitive_merger_interest", policies)

    def test_policy_contract_contains_decisive_precedence_examples(self) -> None:
        contract = policy_contract()
        precedence = " ".join(contract["precedence"])

        self.assertIn("price-reaction", precedence)
        self.assertIn("ATM/shelf/prospectus", precedence)
        self.assertIn("nonbinding LOI", precedence)
        self.assertIn("Routine 13F", precedence)
        self.assertEqual(set(contract["required_output_fields"]), REVIEW_FIELDS)

    def test_review_schema_requires_structured_evidence(self) -> None:
        valid = {
            "review_id": "R5900000000000000000000",
            "label": "ineligible",
            "policy_id": "price_reaction_wrapper",
            "qualifying_event": "none",
            "title_evidence": "stock jumps: here is why",
            "metadata_evidence": "single ticker",
            "precedence": "price reaction wrapper overrides referenced event",
            "confidence": "high",
            "needs_article_body": False,
            "discovered_pattern": "X stock jumps: here is why",
        }
        validate_review_row(valid, "test")

        invalid = dict(valid)
        invalid["needs_article_body"] = "false"
        with self.assertRaisesRegex(ValueError, "not boolean"):
            validate_review_row(invalid, "test")


if __name__ == "__main__":
    unittest.main()
