from __future__ import annotations

import unittest

from research.text_intelligence.news_synthesis_v1.reviewed_title_policy import (
    classify_reviewed_title_policy,
)


class ReviewedForecastTitlePolicyTest(unittest.TestCase):
    def test_earnings_result_templates_are_ineligible(self) -> None:
        titles = (
            "Alpha Q2 EPS $0.25 Beats $0.20 Estimate, Sales $10M Miss $11M Estimate",
            "Alpha Earnings Report: Q2 Overview",
            "Alpha Q2 Earnings Summary: Key Takeaways",
            "Alpha Earnings: What Investors Need To Know",
        )
        for title in titles:
            with self.subTest(title=title):
                decision = classify_reviewed_title_policy(title, tickers=("ALPH",))
                self.assertIsNotNone(decision)
                self.assertEqual(decision.label, "ineligible")

    def test_analyst_forecast_and_routine_fund_holding_are_ineligible(self) -> None:
        titles = (
            "These Analysts Boost Their Forecasts Following Alpha Earnings",
            "Soros Fund Management Raises Share Stake In Alpha To 332,200 Shares",
        )
        for title in titles:
            with self.subTest(title=title):
                decision = classify_reviewed_title_policy(title, tickers=("ALPH",))
                self.assertIsNotNone(decision)
                self.assertEqual(decision.label, "ineligible")

    def test_numeric_guidance_against_estimate_is_ineligible(self) -> None:
        decision = classify_reviewed_title_policy(
            "Alpha Sees Q2 Sales $145M-$155M vs $158.75M Est",
            tickers=("ALPH",),
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.family, "numeric_guidance_vs_estimate")
        self.assertEqual(decision.label, "ineligible")

    def test_single_issuer_clinical_conference_is_eligible(self) -> None:
        decision = classify_reviewed_title_policy(
            "Alpha To Present Phase 2 Clinical Data At ESMO Congress 2026",
            tickers=("ALPH",),
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.label, "eligible")
        self.assertEqual(decision.title_material_flag, "clinical_conference_material")

    def test_multi_ticker_clinical_conference_is_ineligible(self) -> None:
        decision = classify_reviewed_title_policy(
            "Alpha And Beta To Present Clinical Data At ESMO Congress 2026",
            tickers=("ALPH", "BETA"),
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.label, "ineligible")

    def test_reported_earlier_is_ineligible_but_correction_requires_source_review(self) -> None:
        reported = classify_reviewed_title_policy(
            "Reported Earlier, Alpha Raises Guidance", tickers=("ALPH",)
        )
        correction = classify_reviewed_title_policy(
            "CORRECTION: Alpha Raises FY2026 Guidance", tickers=("ALPH",)
        )
        self.assertIsNotNone(reported)
        self.assertEqual(reported.label, "ineligible")
        self.assertIsNone(correction)


if __name__ == "__main__":
    unittest.main()
