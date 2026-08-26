from __future__ import annotations

import unittest

from research.text_intelligence.sec_synthesis_v1 import SecSynthesisEngine, validate_document


class SecSynthesisV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.filing = {
            "accession_number": "0000000000-26-000001",
            "cik": "1000",
            "company_name": "Example Corp",
            "form_type": "10-K",
            "filing_date": "2026-02-20",
            "report_date": "2025-12-31",
            "accepted_at_utc": "2026-02-20T21:00:00Z",
        }
        self.text = "Revenue increased 18 percent and management raised full-year guidance.\nA material weakness remains unresolved."
        self.documents = [{
            "document_id": "doc-1",
            "document_role": "primary",
            "document_type": "10-K",
            "ticker": "EXM",
            "text": self.text,
            "text_sha256": "source-hash",
        }]

    def test_preserves_exact_narrative_evidence(self) -> None:
        result = SecSynthesisEngine().process(
            filing=self.filing,
            documents=self.documents,
            source_hash="revision-1",
        )
        self.assertEqual([], validate_document(result))
        self.assertTrue(result["narrative_disclosures"])
        for disclosure in result["narrative_disclosures"]:
            evidence = disclosure["evidence"][0]
            self.assertEqual(self.text[evidence["start"]:evidence["end"]], evidence["quote"])
        self.assertEqual("mixed", result["synthesis"]["composite_sentiment"])
        forecast = next(row for row in result["eligibility"] if row["product"] == "forecast_trigger")
        self.assertTrue(forecast["eligible"])
        self.assertIn("current_material_narrative_disclosure", forecast["reasons"])

    def test_envelope_only_submission_is_synthesized_and_forecast_ineligible(self) -> None:
        result = SecSynthesisEngine().process(
            filing={**self.filing, "form_type": "4"},
            documents=[{
                "document_id": "ownership.xml",
                "document_role": "primary",
                "document_type": "4",
                "ticker": "EXM",
                "text": "",
            }],
            source_hash="revision-envelope",
        )
        self.assertEqual(1, result["filing_envelope"]["document_count"])
        self.assertEqual(0, result["filing_envelope"]["narrative_document_count"])
        forecast = next(row for row in result["eligibility"] if row["product"] == "forecast_trigger")
        self.assertFalse(forecast["eligible"])
        self.assertIn("no_current_material_forecast_evidence", forecast["blocking_flags"])

    def test_forward_looking_and_signature_boilerplate_do_not_create_disclosures(self) -> None:
        text = (
            "Forward-looking statements are made under the Private Securities Litigation Reform Act.\n"
            "SIGNATURE By: Jane Example Title: Chief Executive Officer."
        )
        result = SecSynthesisEngine().process(
            filing=self.filing,
            documents=[{**self.documents[0], "text": text}],
            source_hash="revision-boilerplate",
        )
        self.assertEqual([], result["narrative_disclosures"])
        forecast = next(row for row in result["eligibility"] if row["product"] == "forecast_trigger")
        self.assertFalse(forecast["eligible"])

    def test_compares_annual_duration_fact_and_reconciles(self) -> None:
        facts = [
            self._fact("current", self.filing["accession_number"], "2025-12-31", 118.0, "FY"),
            self._fact("prior", "0000000000-25-000001", "2024-12-31", 100.0, "FY"),
        ]
        result = SecSynthesisEngine().process(
            filing=self.filing,
            documents=self.documents,
            facts=facts,
            source_hash="revision-2",
        )
        transition = result["fundamental_transitions"][0]
        self.assertEqual("comparable", transition["comparability"])
        self.assertEqual("positive", transition["economic_direction"])
        self.assertAlmostEqual(18.0, transition["percent_change"])
        revenue = next(row for row in result["reconciliation"] if row["concept_family"] == "revenue")
        self.assertEqual("independent_confirmation", revenue["state"])

    def test_non_annual_duration_comparison_fails_closed(self) -> None:
        facts = [
            self._fact("current", self.filing["accession_number"], "2025-09-30", 30.0, "Q3"),
            self._fact("prior", "0000000000-25-000002", "2024-09-30", 25.0, "Q3"),
        ]
        result = SecSynthesisEngine().process(
            filing={**self.filing, "form_type": "10-Q"},
            documents=self.documents,
            facts=facts,
            source_hash="revision-3",
        )
        transition = result["fundamental_transitions"][0]
        self.assertEqual("insufficient_duration_context", transition["comparability"])
        self.assertEqual("unresolved", transition["economic_direction"])
        self.assertIn("limited_xbrl_comparability", result["quality_flags"])

    @staticmethod
    def _fact(fact_id: str, accession: str, period_end: str, value: float, fiscal_period: str) -> dict[str, object]:
        return {
            "company_fact_id": fact_id,
            "accession_number": accession,
            "taxonomy": "us-gaap",
            "tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
            "unit_code": "USD",
            "period_end_date": period_end,
            "fiscal_period": fiscal_period,
            "value": value,
            "filed_at_utc": f"{period_end}T00:00:00Z",
            "accepted_at_utc": f"{period_end}T00:00:00Z",
        }


if __name__ == "__main__":
    unittest.main()
