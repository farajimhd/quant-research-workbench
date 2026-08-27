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
            "Alpha Reports Q2 Comparable Sales Growth Of 4.2%",
            "Alpha Q3 Revenue $125M, Net Income $18M",
            "Alpha Q4 Earnings Top Forecasts, Private Equity Income Surges",
            "Alpha Holiday Sales Surged 2.8% As Digital Revenue Grew",
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
            "Tiger Global Management Takes New Stake In Alpha Stock",
        )
        for title in titles:
            with self.subTest(title=title):
                decision = classify_reviewed_title_policy(title, tickers=("ALPH",))
                self.assertIsNotNone(decision)
                self.assertEqual(decision.label, "ineligible")

    def test_reviewed_context_title_families_are_ineligible(self) -> None:
        cases = {
            "Why Is Alpha Stock Trading Higher Today?": "price_reaction",
            "12 Industrials Stocks Moving In Tuesday's After-Market Session": "roundup_or_reference_list",
            "Alpha Trading Halted At 10:32 ET, News Pending": "trading_halt_status",
            "Could Alpha Stock Double This Year?": "question_or_hypothesis",
            "Alpha Valuation Overview Compared To Its Peers": "valuation_peer_comparison",
            "Alpha Faces Class Action Lawsuit": "legal_regulatory_action",
            "Unusual Options Activity In Alpha Stock": "options_activity",
            "Alpha RSI Signals An Overbought Stock": "technical_trading",
            "Alpha Earnings Preview: What To Expect": "preview_schedule",
        }
        for title, family in cases.items():
            with self.subTest(title=title):
                decision = classify_reviewed_title_policy(title, tickers=("ALPH",))
                self.assertIsNotNone(decision)
                self.assertEqual(decision.label, "ineligible")
                self.assertEqual(decision.family, family)

    def test_live_broadcast_is_eligible(self) -> None:
        decision = classify_reviewed_title_policy(
            "Alpha To Host Live Webcast Of Investor Event", tickers=("ALPH",)
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.label, "eligible")
        self.assertEqual(decision.family, "live_broadcast")

    def test_material_ownership_is_eligible(self) -> None:
        decision = classify_reviewed_title_policy(
            "Activist Investor Files Schedule 13D Reporting 8.7% Stake In Alpha",
            tickers=("ALPH",),
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.label, "eligible")
        self.assertEqual(decision.family, "material_ownership")

    def test_material_event_titles_do_not_match_context_patterns_incidentally(self) -> None:
        titles = (
            "National Bank Holdings Raises Quarterly Dividend To $0.30 Per Share",
            "60 Degrees Pharmaceuticals Announces 1-For-5 Reverse Stock Split",
            "Alpha Enters ATM Offering Agreement And May Sell Up To $6.5M In Common Stock",
            "Alpha Launches $35M Buyback As It Eyes Undervalued Shares",
        )
        for title in titles:
            with self.subTest(title=title):
                self.assertIsNone(classify_reviewed_title_policy(title, tickers=("ALPH",)))

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
