from __future__ import annotations

import unittest

from research.text_intelligence.news_synthesis_v1.forecast_policy import (
    resolve_forecast_policy,
)
from research.text_intelligence.news_synthesis_v1.reviewed_title_policy import (
    ReviewedTitlePolicy,
)


def _envelope(
    *,
    structure: str = "single_subject",
    purpose: str = "report",
    origin: str = "issuer",
) -> dict[str, dict[str, str]]:
    return {
        "document_structure": {"value": structure},
        "communication_purpose": {"value": purpose},
        "information_origin": {"value": origin},
    }


def _provider(route: str = "forecast_candidate", family: str = "unclassified") -> dict[str, str]:
    return {"route": route, "content_family": family}


def _resolve(
    *concepts: str,
    title: str = "Alpha Announces Company Update",
    envelope: dict[str, dict[str, str]] | None = None,
    reviewed: ReviewedTitlePolicy | None = None,
    provider: dict[str, str] | None = None,
    transcript: bool = False,
):
    return resolve_forecast_policy(
        title=title,
        text=title,
        tickers=("AAA",),
        envelope=envelope or _envelope(),
        statements=({"concept_leaf": concept} for concept in concepts),
        reviewed_title_policy=reviewed,
        provider_context=provider or _provider(),
        earnings_call_material=transcript,
    )


class NewsForecastPolicyTest(unittest.TestCase):
    def test_source_material_precedes_context_metadata(self) -> None:
        transcript = _resolve(
            "earnings.performance",
            provider=_provider("context_only", "earnings_result"),
            transcript=True,
        )
        clinical = _resolve(
            reviewed=ReviewedTitlePolicy(
                "eligible", "single_issuer_clinical_conference", "report", "issuer"
            ),
            provider=_provider("context_only", "conference_preview"),
        )
        self.assertEqual(transcript.label, "eligible")
        self.assertEqual(clinical.label, "eligible")

    def test_title_and_provider_context_exclusions_precede_event_concepts(self) -> None:
        title_block = _resolve(
            "commercial.contract",
            reviewed=ReviewedTitlePolicy("ineligible", "price_reaction", "explain_move", "editorial"),
        )
        metadata_block = _resolve(
            "commercial.contract",
            provider=_provider("context_only", "movers_roundup"),
        )
        self.assertEqual(title_block.label, "ineligible")
        self.assertEqual(metadata_block.label, "ineligible")

    def test_multi_subject_and_contextual_purpose_fail_closed(self) -> None:
        multi = _resolve("commercial.contract", envelope=_envelope(structure="multi_subject"))
        recap = _resolve("commercial.contract", envelope=_envelope(purpose="recap"))
        self.assertEqual(multi.label, "ineligible")
        self.assertEqual(recap.label, "ineligible")

    def test_approved_event_matrix(self) -> None:
        cases = (
            (("clinical.trial_result",), "Alpha Trial Met Primary Endpoint", "eligible", "clinical_event"),
            (("guidance.issued",), "Alpha Raises FY2027 Guidance", "eligible", "issuer_guidance"),
            (("earnings.performance",), "Alpha Reports Q2 Results", "ineligible", "earnings_results"),
            (("legal.proceeding",), "Alpha Enters Settlement", "ineligible", "legal_or_regulatory_action"),
            (("commercial.contract",), "Alpha Wins Supply Contract", "eligible", "direct_material_issuer_event"),
        )
        for concepts, title, label, family in cases:
            with self.subTest(title=title):
                decision = _resolve(*concepts, title=title)
                self.assertEqual((decision.label, decision.family), (label, family))

    def test_incidental_contract_concept_requires_focal_title_evidence(self) -> None:
        decision = _resolve(
            "commercial.contract", "legal.proceeding",
            title="Regulator Takes Enforcement Action Against Alpha",
        )
        self.assertEqual(decision.label, "ineligible")
        self.assertEqual(decision.family, "legal_or_regulatory_action")

    def test_negative_or_product_language_does_not_count_as_direct_action(self) -> None:
        wont = _resolve(
            "commercial.contract", title="Alpha Won't Add Fact Checks Under New Law"
        )
        sold_out = _resolve(
            "product.milestone", title="Alpha Product Sells Out Instantly"
        )
        self.assertEqual(wont.label, "ineligible")
        self.assertEqual(sold_out.label, "ineligible")

    def test_account_takeover_does_not_invoke_transaction_policy(self) -> None:
        decision = _resolve(
            "corporate_transaction.acquisition", "commercial.partnership",
            title="Alpha Partners With Beta To Address Account Takeover Risks",
        )
        self.assertEqual(decision.label, "eligible")
        self.assertEqual(decision.family, "direct_material_issuer_event")

    def test_earnings_results_override_embedded_guidance(self) -> None:
        decision = _resolve(
            "earnings.performance", "guidance.issued",
            title="Alpha Reports Q2 Results And Issues Guidance",
            reviewed=ReviewedTitlePolicy(
                "ineligible", "earnings_result_or_recap", "recap", "editorial"
            ),
        )
        self.assertEqual(decision.label, "ineligible")
        self.assertEqual(decision.family, "earnings_result_or_recap")

    def test_direct_guidance_precedes_incidental_earnings_concept(self) -> None:
        decision = _resolve(
            "earnings.performance", "guidance.issued",
            title="Alpha Reiterates FY2026 Revenue Outlook",
        )
        self.assertEqual(decision.label, "eligible")
        self.assertEqual(decision.family, "issuer_guidance")

    def test_mixed_families_require_direct_issuer_action(self) -> None:
        direct = _resolve("capital.financing", title="Alpha Announces $20M Financing")
        indirect = _resolve("capital.financing", title="Financing Options For Alpha")
        self.assertEqual(direct.label, "eligible")
        self.assertEqual(indirect.label, "ineligible")

    def test_direct_mixed_event_precedes_incidental_context_concepts(self) -> None:
        cases = (
            ("Alpha Increases Quarterly Dividend To $0.30", "capital.return"),
            ("Alpha Invests $300M In New AI Data Center", "capital.financing"),
            ("Alpha Launches New Diagnostic Platform", "product.milestone"),
            ("Alpha Deploys 50 Additional Retail Units", "operations.capacity_change"),
            ("Alpha Redeems All Outstanding Warrants", "capital.structure"),
        )
        for title, concept in cases:
            with self.subTest(title=title):
                decision = _resolve(concept, "market.context", title=title)
                self.assertEqual(decision.label, "eligible")
                self.assertEqual(decision.family, "direct_mixed_family_event")

    def test_transactions_require_definitive_non_speculative_language(self) -> None:
        definitive = _resolve(
            "corporate_transaction.acquisition",
            title="Alpha Signs Definitive Agreement To Acquire Beta",
        )
        speculative = _resolve(
            "corporate_transaction.acquisition",
            title="Alpha Could Consider A Potential Acquisition",
        )
        self.assertEqual(definitive.label, "eligible")
        self.assertEqual(speculative.label, "ineligible")

    def test_definitive_transaction_is_not_negated_by_potential_milestone_payments(self) -> None:
        decision = _resolve(
            "corporate_transaction.acquisition", "earnings.performance",
            title=(
                "Alpha Agreed To Acquire Beta For $250M Upfront And Up To $750M "
                "In Potential Milestone Payments"
            ),
        )
        self.assertEqual(decision.label, "eligible")
        self.assertEqual(decision.family, "definitive_transaction")

    def test_incidental_acquisition_concept_does_not_veto_focal_investment(self) -> None:
        decision = _resolve(
            "corporate_transaction.acquisition", "capital.financing",
            title="Alpha Invests $300 Million In AI Data Center",
        )
        self.assertEqual(decision.label, "eligible")
        self.assertEqual(decision.family, "direct_investment_event")

    def test_material_ownership_is_distinct_from_routine_holdings(self) -> None:
        material = _resolve("ownership.position", title="Activist Investor Reports 8.2% Stake In Alpha")
        routine = _resolve("ownership.position", title="Fund Reports Position In Alpha")
        self.assertEqual(material.label, "eligible")
        self.assertEqual(routine.label, "ineligible")


if __name__ == "__main__":
    unittest.main()
