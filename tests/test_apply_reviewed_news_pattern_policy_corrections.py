from __future__ import annotations

import unittest

from scripts.apply_reviewed_news_pattern_policy_corrections import (
    resolve_assignment_label,
)


def row(*, gold: str, eligible: str = "", ineligible: str = "", title: str = "Title", tickers: str = "AAA", source_id: str = "id") -> dict[str, str]:
    return {
        "source_id": source_id,
        "gold_label": gold,
        "eligible_policy_patterns": eligible,
        "ineligible_policy_patterns": ineligible,
        "tickers": tickers,
        "title": title,
    }


class ReviewedNewsPatternPolicyCorrectionsTest(unittest.TestCase):
    def decide(self, value: dict[str, str], edits: dict[str, str] | None = None) -> tuple[str, str, str]:
        return resolve_assignment_label(value, manual_reported_earlier_edits=edits or {})

    def test_unanimous_policy_corrects_binary_gold(self) -> None:
        decision = self.decide(row(gold="eligible", ineligible="event.earnings_results"))
        self.assertEqual(decision[0], "ineligible")

    def test_reviewed_title_policy_overrides_unanimous_metadata_policy(self) -> None:
        decision = self.decide(row(
            gold="eligible",
            eligible="event.guidance_outlook",
            title="Alpha Sees Q2 Sales $145M-$155M vs $158.75M Est",
        ))
        self.assertEqual(decision[0], "ineligible")
        self.assertEqual(decision[1], "title_policy")

    def test_transcript_overrides_earnings_result(self) -> None:
        decision = self.decide(row(
            gold="ineligible",
            eligible="context.earnings_call_transcript",
            ineligible="event.earnings_results",
        ))
        self.assertEqual(decision[0], "eligible")

    def test_guidance_result_and_preview_conflicts_are_ineligible(self) -> None:
        for ineligible in ("event.earnings_results", "context.preview_schedule", "signal.question"):
            with self.subTest(ineligible=ineligible):
                decision = self.decide(row(
                    gold="eligible",
                    eligible="event.guidance_issued",
                    ineligible=ineligible,
                ))
                self.assertEqual(decision[0], "ineligible")

    def test_audited_material_ownership_conflict_is_eligible(self) -> None:
        decision = self.decide(row(
            gold="ineligible",
            eligible="event.ownership_material",
            ineligible="context.portfolio_holdings_trade",
        ))
        self.assertEqual(decision[0], "eligible")

    def test_clinical_nonissuer_review_uses_audited_strata(self) -> None:
        single = self.decide(row(
            gold="eligible",
            eligible="event.clinical_conference_preview",
            ineligible="context.nonissuer_politics_lifestyle",
        ))
        multi = self.decide(row(
            gold="eligible",
            eligible="event.clinical_conference_preview",
            ineligible="context.nonissuer_politics_lifestyle",
            tickers="AAA|BBB",
        ))
        corrected = self.decide(row(
            gold="ineligible",
            eligible="event.clinical_conference_preview",
            ineligible="context.nonissuer_politics_lifestyle",
            tickers="AAA|BBB",
        ))
        self.assertEqual(single[0], "eligible")
        self.assertEqual(multi[0], "ineligible")
        self.assertEqual(corrected[0], "eligible")

    def test_reported_earlier_manual_edit_overrides_family_rule(self) -> None:
        value = row(
            source_id="edited",
            gold="eligible",
            eligible="event.guidance_issued",
            ineligible="context.reported_earlier",
            title="CORRECTION: Alpha Sees FY2026 Sales $1B",
        )
        self.assertEqual(self.decide(value, {"edited": "ineligible"})[0], "ineligible")
        self.assertEqual(self.decide(value)[0], "eligible")

    def test_unreviewed_nonbinary_gold_is_preserved(self) -> None:
        decision = self.decide(row(gold="unresolved", ineligible="event.earnings_results"))
        self.assertEqual(decision[0], "unresolved")


if __name__ == "__main__":
    unittest.main()
