from __future__ import annotations

import unittest

from research.text_intelligence.news_synthesis_v1.review_spec import compile_review_spec


class ReviewSpecTest(unittest.TestCase):
    def test_duplicate_envelope_quote_binds_first_exact_occurrence(self) -> None:
        article = {
            "sample_id": "N1", "source_id": "s", "source_timestamp": "t",
            "source_text_sha256": "a" * 64,
            "rendered_product": {"text": "-Reuters\nbody\n-Reuters"},
        }
        spec = {
            "sample_id": "N1", "review_notes": "Reviewed duplicate source boilerplate.",
            "envelope": {field: {"value": value, "evidence": ["-Reuters"]} for field, value in {
                "document_structure": "single_subject", "communication_purpose": "report",
                "information_origin": "editorial", "production_method": "aggregated",
                "text_availability": "rendered",
            }.items()},
            "entities": [], "statements": [],
        }
        document = compile_review_spec(article, spec)
        for decision in document["envelope"].values():
            self.assertEqual(decision["evidence"][0]["start"], 0)

    def test_compiles_exact_source_bound_atomic_statement(self) -> None:
        article = {
            "sample_id": "N1", "source_id": "source", "source_timestamp": "2026-01-01T12:00:00Z",
            "source_text_sha256": "b" * 64,
            "rendered_product": {"text": "ABC raised guidance to $50 million, up 25%, for January 31 at 08:30 ET."},
        }
        spec = {
            "sample_id": "N1", "review_notes": "Reviewed source and separated the guidance event.",
            "envelope": {
                "document_structure": {"value": "single_subject", "evidence": ["ABC raised guidance to $50 million, up 25%, for January 31 at 08:30 ET."]},
                "communication_purpose": {"value": "report", "evidence": ["ABC raised guidance to $50 million, up 25%, for January 31 at 08:30 ET."]},
                "information_origin": {"value": "issuer", "evidence": ["ABC raised guidance to $50 million, up 25%, for January 31 at 08:30 ET."]},
                "production_method": {"value": "original", "evidence": []},
                "text_availability": {"value": "rendered", "evidence": ["ABC raised guidance to $50 million, up 25%, for January 31 at 08:30 ET."]},
            },
            "entities": [{"entity_id": "security:ABC", "entity_kind": "security", "display_name": "ABC", "ticker": "ABC", "identity_status": "resolved", "identity_evidence": ["ABC"]}],
            "statements": [{
                "statement_kind": "event", "concept_leaf": "guidance.issued", "epistemic_status": "confirmed", "time_relation": "current",
                "evidence": ["ABC raised guidance to $50 million, up 25%, for January 31 at 08:30 ET."],
                "participations": [{"entity_id": "security:ABC", "semantic_role": "affected_subject", "discourse_role": "none", "semantic_sentiment": "positive", "sentiment_strength": 3}],
            }],
        }
        document = compile_review_spec(article, spec)
        self.assertEqual(document["statements"][0]["typed_facts"], [
            {"fact_type": "money", "raw": "$50 million", "value": "50", "currency": "USD", "magnitude": "million"},
            {"fact_type": "percentage", "raw": "25%", "value": "25"},
            {"fact_type": "date", "raw": "January 31"},
            {"fact_type": "time", "raw": "08:30 ET"},
        ])
        self.assertTrue(next(row for row in document["eligibility"] if row["product"] == "forecast_trigger")["eligible"])

    def test_duplicate_quote_requires_occurrence(self) -> None:
        article = {"sample_id": "N1", "source_id": "s", "source_timestamp": "t", "source_text_sha256": "c" * 64, "rendered_product": {"text": "same same"}}
        spec = {
            "sample_id": "N1",
            "envelope": {field: {"value": value, "evidence": []} for field, value in {
                "document_structure": "single_subject", "communication_purpose": "report", "information_origin": "unknown", "production_method": "unknown", "text_availability": "rendered",
            }.items()},
            "entities": [],
            "statements": [{"statement_kind": "reference", "concept_leaf": "market.context", "epistemic_status": "confirmed", "time_relation": "current", "evidence": ["same"], "participations": []}],
        }
        with self.assertRaisesRegex(RuntimeError, "must be unique"):
            compile_review_spec(article, spec)

    def test_ticker_shorthand_uses_point_in_time_identity(self) -> None:
        article = {
            "sample_id": "N1", "source_id": "s", "source_timestamp": "t",
            "source_text_sha256": "d" * 64,
            "publication": {"title": "ABC won a contract."},
            "rendered_product": {"text": "ABC won a contract."},
            "point_in_time_issuer_candidates": [{
                "display_symbol": "ABC",
                "identity_evidence": ["issuer_alias:abc_corporation", "symbol:ABC"],
            }],
        }
        spec = {
            "sample_id": "N1", "review_notes": "Reviewed.",
            "envelope": {field: {"value": value, "evidence": []} for field, value in {
                "document_structure": "single_subject", "communication_purpose": "report",
                "information_origin": "issuer", "production_method": "original", "text_availability": "rendered",
            }.items()},
            "entities": [{"ticker": "ABC"}],
            "statements": [{
                "statement_kind": "event", "concept_leaf": "commercial.contract",
                "epistemic_status": "confirmed", "time_relation": "current",
                "evidence": ["ABC won a contract."],
                "participations": [{"ticker": "ABC", "semantic_role": "affected_subject", "discourse_role": "none", "semantic_sentiment": "positive", "sentiment_strength": 3}],
            }],
        }
        document = compile_review_spec(article, spec)
        self.assertEqual(document["entities"][0]["entity_id"], "security:ABC")
        self.assertEqual(document["entities"][0]["display_name"], "Abc Corporation")
        self.assertEqual(document["participations"][0]["entity_id"], "security:ABC")

    def test_bare_ticker_shorthand_is_review_input_only(self) -> None:
        article = {
            "sample_id": "N1", "source_id": "s", "source_timestamp": "t",
            "source_text_sha256": "d" * 64,
            "publication": {"title": "ABC won a contract."},
            "rendered_product": {"text": "ABC won a contract."},
            "point_in_time_issuer_candidates": [{
                "ticker": "ABC", "identity_evidence": ["issuer_alias:Alpha_Beta_Corp"],
            }],
        }
        spec = {
            "sample_id": "N1", "review_notes": "manual",
            "envelope": {field: {"value": value, "evidence": []} for field, value in {
                "document_structure": "single_subject", "communication_purpose": "report",
                "information_origin": "issuer", "production_method": "original",
                "text_availability": "rendered",
            }.items()},
            "entities": ["ABC"],
            "statements": [{
                "statement_kind": "event", "concept_leaf": "commercial.contract",
                "epistemic_status": "confirmed", "time_relation": "current",
                "evidence": ["ABC won a contract."],
                "participations": [{
                    "entity_id": "ABC", "semantic_role": "affected_subject",
                    "discourse_role": "none", "semantic_sentiment": "positive",
                    "sentiment_strength": 3,
                }],
            }],
        }
        document = compile_review_spec(article, spec)
        self.assertEqual(document["entities"][0]["entity_id"], "security:ABC")
        self.assertEqual(document["participations"][0]["entity_id"], "security:ABC")
        self.assertEqual(document["entities"][0]["display_name"], "Alpha Beta Corp")

    def test_accounting_currency_value_is_preserved(self) -> None:
        from research.text_intelligence.news_synthesis_v1.facts import extract_typed_facts

        facts = extract_typed_facts([{"quote": "EPS £(0.04), financing €2.5M and $10B"}])
        self.assertEqual(facts, [
            {"fact_type": "money", "raw": "£(0.04)", "value": "-0.04", "currency": "GBP", "magnitude": "units"},
            {"fact_type": "money", "raw": "€2.5M", "value": "2.5", "currency": "EUR", "magnitude": "million"},
            {"fact_type": "money", "raw": "$10B", "value": "10", "currency": "USD", "magnitude": "billion"},
        ])

    def test_generic_numbers_keep_magnitudes_and_reject_identifiers(self) -> None:
        from research.text_intelligence.news_synthesis_v1.facts import extract_typed_facts

        facts = extract_typed_facts([{"quote": "Confidence 62 vs 63; 750K shares under X-17A-5 at $4 and 2.5%."}])
        self.assertEqual(facts, [
            {"fact_type": "money", "raw": "$4", "value": "4", "currency": "USD", "magnitude": "units"},
            {"fact_type": "percentage", "raw": "2.5%", "value": "2.5"},
            {"fact_type": "number", "raw": "62", "value": "62", "magnitude": "units"},
            {"fact_type": "number", "raw": "63", "value": "63", "magnitude": "units"},
            {"fact_type": "number", "raw": "750K", "value": "750", "magnitude": "thousand"},
        ])

    def test_security_cannot_be_claim_source(self) -> None:
        article = {
            "sample_id": "N1", "source_id": "s", "source_timestamp": "t",
            "source_text_sha256": "e" * 64,
            "rendered_product": {"text": "ABC issued guidance."},
            "point_in_time_issuer_candidates": [{"display_symbol": "ABC", "identity_evidence": ["issuer_alias:abc"]}],
        }
        spec = {
            "sample_id": "N1", "review_notes": "Reviewed.",
            "envelope": {field: {"value": value, "evidence": []} for field, value in {
                "document_structure": "single_subject", "communication_purpose": "report", "information_origin": "issuer",
                "production_method": "unknown", "text_availability": "rendered",
            }.items()},
            "entities": [{"ticker": "ABC"}],
            "statements": [{
                "statement_kind": "event", "concept_leaf": "guidance.issued", "epistemic_status": "confirmed", "time_relation": "current",
                "evidence": ["ABC issued guidance."],
                "participations": [{"ticker": "ABC", "semantic_role": "affected_subject", "discourse_role": "claim_source", "semantic_sentiment": "neutral", "sentiment_strength": 0}],
            }],
        }
        with self.assertRaisesRegex(RuntimeError, "claim_source_entity_kind"):
            compile_review_spec(article, spec)


if __name__ == "__main__":
    unittest.main()
