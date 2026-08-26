from __future__ import annotations

import unittest

from scripts import apply_reviewed_title_family_label_corrections as correction


class ReviewedTitleFamilyLabelCorrectionTest(unittest.TestCase):
    def test_reviewed_price_reaction_patterns_are_ineligible(self) -> None:
        titles = (
            "Enovix Shares Are Moving Higher After The Bell: What's Going On?",
            "What's Going On With Bitcoin Mining Stocks CleanSpark And Riot Platforms?",
            "What's Behind The Drop In Inovio Pharmaceuticals Stock Today?",
        )
        for title in titles:
            with self.subTest(title=title):
                decision = correction.desired_title_family_label(title)
                self.assertIsNotNone(decision)
                self.assertEqual(decision[0], "ineligible")
                self.assertTrue(decision[1].startswith("price_reaction:"))

    def test_transcript_and_call_quotes_are_eligible(self) -> None:
        titles = (
            "American Express Q3 FY2025 Earnings Call Transcript",
            "During Conf. Call, Alpha CFO Says Demand Remains Strong",
        )
        for title in titles:
            with self.subTest(title=title):
                decision = correction.desired_title_family_label(title)
                self.assertIsNotNone(decision)
                self.assertEqual(decision[0], "eligible")
                self.assertTrue(decision[1].startswith("earnings_call:"))

    def test_unrelated_transcript_is_not_an_earnings_call(self) -> None:
        self.assertIsNone(correction.desired_title_family_label(
            "Court Transcript Shows Communications With Engineer"
        ))

    def test_correction_preserves_provenance_and_records_superseded_label(self) -> None:
        original = {
            "source_id": "row-1",
            "forecast_eligibility_label": "ineligible",
            "forecast_eligible": False,
            "source_revision_key": "revision",
        }

        corrected = correction.corrected_label_row(
            original,
            desired_label="eligible",
            title_pattern="earnings_call:transcript",
            title="Alpha Earnings Call Transcript",
            directly_reviewed=True,
        )

        self.assertEqual(corrected["forecast_eligibility_label"], "eligible")
        self.assertTrue(corrected["forecast_eligible"])
        self.assertEqual(corrected["superseded_forecast_eligibility_label"], "ineligible")
        self.assertEqual(corrected["source_revision_key"], "revision")
        self.assertEqual(corrected["authority_class"], "operator_manual_title_review")

    def test_successors_do_not_mutate_v2_parents(self) -> None:
        self.assertTrue(correction.PARENT_TRAINING.name.endswith("_v2"))
        self.assertNotEqual(correction.PARENT_TRAINING, correction.DEFAULT_TRAINING_OUTPUT)
        self.assertNotEqual(correction.PARENT_HOLDOUT, correction.DEFAULT_HOLDOUT_OUTPUT)


if __name__ == "__main__":
    unittest.main()
