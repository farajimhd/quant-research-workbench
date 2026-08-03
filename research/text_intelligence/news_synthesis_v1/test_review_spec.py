from __future__ import annotations

import unittest

from research.text_intelligence.news_synthesis_v1.review_spec import (
    compile_approved_draft,
    compile_review_spec,
)


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

    def test_historical_benzinga_euro_marker_is_not_usd(self) -> None:
        from research.text_intelligence.news_synthesis_v1.facts import extract_typed_facts

        facts = extract_typed_facts([{"quote": "AbbVie issued E$3.6B of debt."}])
        self.assertEqual(facts, [{
            "fact_type": "money", "raw": "E$3.6B", "value": "3.6",
            "currency": "EUR", "magnitude": "billion",
        }])

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

    def test_percentage_range_preserves_both_bounds(self) -> None:
        from research.text_intelligence.news_synthesis_v1.facts import extract_typed_facts

        facts = extract_typed_facts([{
            "quote": "Sales growth 18-22%, licensing 10%-20%, and margin 40 to 44 percent."
        }])
        self.assertEqual(facts, [
            {
                "fact_type": "percentage_range", "raw": "18-22%",
                "lower_value": "18", "upper_value": "22",
            },
            {
                "fact_type": "percentage_range", "raw": "10%-20%",
                "lower_value": "10", "upper_value": "20",
            },
            {
                "fact_type": "percentage_range", "raw": "40 to 44 percent",
                "lower_value": "40", "upper_value": "44",
            },
        ])

    def test_time_with_seconds_is_one_fact(self) -> None:
        from research.text_intelligence.news_synthesis_v1.facts import extract_typed_facts

        self.assertEqual(
            extract_typed_facts([{"quote": "Halted at 7:50:00 p.m. ET"}]),
            [{"fact_type": "time", "raw": "7:50:00 p.m. ET"}],
        )

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

    def test_approved_draft_removes_migration_and_refreshes_derived_products(self) -> None:
        article = {
            "sample_id": "N1", "source_id": "source",
            "source_timestamp": "2026-01-01T12:00:00Z",
            "source_text_sha256": "f" * 64,
            "rendered_product": {"text": "ABC raised guidance to E$3.6B."},
        }
        spec = {
            "sample_id": "N1", "review_notes": "Reviewed.",
            "envelope": {
                field: {"value": value, "evidence": ["ABC raised guidance to E$3.6B."]}
                for field, value in {
                    "document_structure": "single_subject",
                    "communication_purpose": "report",
                    "information_origin": "issuer",
                    "production_method": "original",
                    "text_availability": "rendered",
                }.items()
            },
            "entities": [{
                "entity_id": "issuer:abc", "entity_kind": "issuer",
                "display_name": "ABC", "identity_status": "resolved",
                "identity_evidence": ["ABC"],
            }],
            "statements": [{
                "statement_kind": "event", "concept_leaf": "guidance.issued",
                "epistemic_status": "confirmed", "time_relation": "current",
                "evidence": ["ABC raised guidance to E$3.6B."],
                "participations": [{
                    "entity_id": "issuer:abc", "semantic_role": "affected_subject",
                    "discourse_role": "none", "semantic_sentiment": "positive",
                    "sentiment_strength": 3,
                }],
            }],
        }
        draft = compile_review_spec(article, spec)
        draft["concept_registry_version"] = "stale"
        draft["statements"][0]["typed_facts"] = []
        draft["statements"][0]["evidence_spans"][0]["source_field"] = "title"
        draft["statements"][0]["evidence_spans"][0]["start"] = 99
        draft["statements"][0]["evidence_spans"][0]["end"] = 130
        draft["issuer_views"] = []
        draft["synthesis"] = {"wrong": True}
        draft["eligibility"] = []

        approved = compile_approved_draft(article, draft)

        self.assertIn("migration", approved)
        self.assertNotEqual(approved["concept_registry_version"], "stale")
        self.assertTrue(all(
            row["rule_id"] == "manual_review_v1_approved_draft"
            for row in approved["envelope"].values()
        ))
        self.assertEqual(approved["statements"][0]["typed_facts"], [{
            "fact_type": "money", "raw": "E$3.6B", "value": "3.6",
            "currency": "EUR", "magnitude": "billion",
        }])
        self.assertEqual(
            approved["statements"][0]["evidence_spans"][0]["source_field"],
            "rendered_text",
        )
        self.assertEqual(len(approved["issuer_views"]), 1)
        self.assertEqual(
            approved["synthesis"]["renderer_version"],
            "news_synthesis_renderer_v1",
        )
        self.assertTrue(any(row["product"] == "forecast_trigger" for row in approved["eligibility"]))

    def test_approved_draft_rejects_fallback_concept(self) -> None:
        article = {"sample_id": "N1"}
        draft = {"sample_id": "N1", "statements": [{"concept_leaf": "unclassified"}]}
        with self.assertRaisesRegex(RuntimeError, "unresolved concept"):
            compile_approved_draft(article, draft)

    def test_approved_draft_preserves_exact_repeated_evidence_occurrence(self) -> None:
        article = {
            "sample_id": "N1", "source_id": "source",
            "source_timestamp": "2026-01-01T12:00:00Z",
            "source_text_sha256": "f" * 64,
            "rendered_product": {"text": "Repeated claim.\nRepeated claim."},
        }
        spec = {
            "sample_id": "N1", "review_notes": "Reviewed.",
            "envelope": {
                field: {"value": value, "evidence": []}
                for field, value in {
                    "document_structure": "single_subject",
                    "communication_purpose": "report",
                    "information_origin": "issuer",
                    "production_method": "original",
                    "text_availability": "rendered",
                }.items()
            },
            "entities": [{
                "entity_id": "issuer:abc", "entity_kind": "issuer",
                "display_name": "ABC", "identity_status": "resolved",
                "identity_evidence": ["ABC"],
            }],
            "statements": [{
                "statement_kind": "event", "concept_leaf": "guidance.issued",
                "epistemic_status": "confirmed", "time_relation": "current",
                "evidence": [{"quote": "Repeated claim.", "occurrence": 2}],
                "participations": [{
                    "entity_id": "issuer:abc", "semantic_role": "affected_subject",
                    "discourse_role": "none", "semantic_sentiment": "neutral",
                    "sentiment_strength": 0,
                }],
            }],
        }
        draft = compile_review_spec(article, spec)
        approved = compile_approved_draft(article, draft)
        span = approved["statements"][0]["evidence_spans"][0]
        self.assertEqual(span["start"], len("Repeated claim.\n"))
        self.assertEqual(span["quote"], "Repeated claim.")

        draft["statements"][0]["evidence_spans"][0]["start"] = 0
        draft["statements"][0]["evidence_spans"][0]["quote"] = "Missing"
        with self.assertRaisesRegex(RuntimeError, "no longer matches"):
            compile_approved_draft(article, draft)

    def test_approved_draft_rebinds_verified_legacy_teaser_evidence(self) -> None:
        article = {
            "sample_id": "N1", "source_id": "source",
            "source_timestamp": "2026-01-01T12:00:00Z",
            "source_text_sha256": "f" * 64,
            "publication": {"teaser": "Repeated claim."},
            "rendered_product": {"text": "Repeated claim.\nRepeated claim."},
        }
        spec = {
            "sample_id": "N1", "review_notes": "Reviewed.",
            "envelope": {
                field: {"value": value, "evidence": []}
                for field, value in {
                    "document_structure": "single_subject",
                    "communication_purpose": "report",
                    "information_origin": "issuer",
                    "production_method": "original",
                    "text_availability": "rendered",
                }.items()
            },
            "entities": [{
                "entity_id": "issuer:abc", "entity_kind": "issuer",
                "display_name": "ABC", "identity_status": "resolved",
                "identity_evidence": ["ABC"],
            }],
            "statements": [{
                "statement_kind": "event", "concept_leaf": "guidance.issued",
                "epistemic_status": "confirmed", "time_relation": "current",
                "evidence": [{"quote": "Repeated claim.", "occurrence": 2}],
                "participations": [{
                    "entity_id": "issuer:abc", "semantic_role": "affected_subject",
                    "discourse_role": "none", "semantic_sentiment": "neutral",
                    "sentiment_strength": 0,
                }],
            }],
        }
        draft = compile_review_spec(article, spec)
        draft["statements"][0]["evidence_spans"][0].update({
            "source_field": "teaser", "start": 0, "end": len("Repeated claim."),
        })
        approved = compile_approved_draft(article, draft)
        span = approved["statements"][0]["evidence_spans"][0]
        self.assertEqual(span["source_field"], "rendered_text")
        self.assertEqual(span["start"], 0)


if __name__ == "__main__":
    unittest.main()
