from __future__ import annotations

import unittest

from research.text_intelligence.news_synthesis_v1.registry import ConceptRegistry, REGISTRY_PATH
from research.text_intelligence.news_synthesis_v1.review_spec import (
    _apply_issuer_view_overrides,
    compile_review_spec,
    materialize_review_spec,
)
from research.text_intelligence.news_synthesis_v1.run_certify_review_specs import parse_source_specs


class ReviewSpecTest(unittest.TestCase):
    def test_manual_issuer_view_override_requires_dominant_evidence(self) -> None:
        views = [{
            "entity_id": "security:ABC",
            "composite_sentiment": "mixed",
            "positive_strength": 2,
            "negative_strength": 3,
        }]
        _apply_issuer_view_overrides(views, [{
            "entity_id": "security:ABC",
            "composite_sentiment": "negative",
            "reason": "Negative evidence is materially stronger overall.",
        }])
        self.assertEqual(views[0]["composite_sentiment"], "negative")

        with self.assertRaisesRegex(RuntimeError, "dominant positive"):
            _apply_issuer_view_overrides(views, [{
                "entity_id": "security:ABC",
                "composite_sentiment": "positive",
                "reason": "Invalid reversal.",
            }])

    def test_certification_parser_accepts_multiline_object_with_unindented_children(self) -> None:
        payload = '{"sample_id":"N0001","statements":[\n{"concept_leaf":"market.context"}\n]}'
        self.assertEqual(parse_source_specs(payload)[0]["sample_id"], "N0001")

    def test_certification_parser_accepts_jsonl(self) -> None:
        payload = '{"sample_id":"N0001"}\n{"sample_id":"N0002"}\n'
        self.assertEqual([row["sample_id"] for row in parse_source_specs(payload)], ["N0001", "N0002"])

    def test_capital_deleveraging_is_registered(self) -> None:
        registry = ConceptRegistry.load(REGISTRY_PATH)
        self.assertTrue(registry.contains("capital.deleveraging"))

    def test_tax_expense_is_registered(self) -> None:
        registry = ConceptRegistry.load(REGISTRY_PATH)
        self.assertTrue(registry.contains("financial.tax_expense"))

    def test_technical_analysis_is_registered(self) -> None:
        registry = ConceptRegistry.load(REGISTRY_PATH)
        self.assertTrue(registry.contains("market.technical_analysis"))

    def test_conflict_of_interest_is_registered(self) -> None:
        registry = ConceptRegistry.load(REGISTRY_PATH)
        self.assertTrue(registry.contains("governance.conflict_of_interest"))

    def test_registry_includes_executive_compensation_without_overloading_management_change(self) -> None:
        registry = ConceptRegistry.load(REGISTRY_PATH)
        self.assertTrue(registry.contains("governance.executive_compensation"))
        self.assertNotEqual(
            registry.resolve("executive_compensation")[0],
            "governance.management_change",
        )

    def test_observed_market_moves_expand_to_neutral_atomic_statements(self) -> None:
        article, spec = self._mover_fixture()
        spec["observed_market_moves"] = [
            {
                "ticker": "ACME",
                "evidence": "Acme (NASDAQ:ACME) shares rose 8% to $4.20.",
            }
        ]

        document = compile_review_spec(article, spec)

        self.assertEqual(document["statements"][0]["statement_kind"], "market_observation")
        self.assertEqual(document["statements"][0]["concept_leaf"], "market.price_move_observed")
        self.assertEqual(document["participations"][0]["semantic_sentiment"], "neutral")
        self.assertEqual(document["participations"][0]["sentiment_strength"], 0)

    def test_materialized_review_spec_rebuilds_without_migration_draft(self) -> None:
        article, spec = self._mover_fixture()
        spec["observed_market_moves"] = [{
            "ticker": "ACME",
            "evidence": "Acme (NASDAQ:ACME) shares rose 8% to $4.20.",
        }]
        document = compile_review_spec(article, spec)
        document["certification"] = {"review_notes": "Complete source review."}

        materialized = materialize_review_spec(article, document)
        rebuilt = compile_review_spec(article, materialized)

        self.assertIn("envelope", materialized)
        self.assertNotIn("approval", materialized)
        self.assertEqual(rebuilt["issuer_views"], document["issuer_views"])
        self.assertEqual(rebuilt["eligibility"], document["eligibility"])

    def test_observed_market_moves_reject_extra_semantic_overrides(self) -> None:
        article, spec = self._mover_fixture()
        spec["observed_market_moves"] = [
            {"ticker": "ACME", "evidence": "Acme rose.", "semantic_sentiment": "positive"}
        ]
        with self.assertRaisesRegex(RuntimeError, "only ticker and evidence"):
            compile_review_spec(article, spec)

    @staticmethod
    def _mover_fixture() -> tuple[dict, dict]:
        article = {
            "sample_id": "N1", "source_id": "source",
            "source_timestamp": "2026-01-01T12:00:00Z",
            "source_text_sha256": "e" * 64,
            "rendered_product": {"text": "Acme (NASDAQ:ACME) shares rose 8% to $4.20."},
            "point_in_time_issuer_candidates": [{
                "ticker": "ACME", "identity_evidence": ["issuer_alias:acme", "symbol:ACME"],
            }],
        }
        spec = {
            "sample_id": "N1", "review_notes": "Reviewed.",
            "envelope": {
                "document_structure": "multi_subject_digest",
                "communication_purpose": "recap",
                "information_origin": "editorial",
                "production_method": "automated",
                "text_availability": "rendered",
            },
            "entities": [{"ticker": "ACME"}],
            "statements": [],
        }
        return article, spec

    def test_compact_scalar_envelope_decisions_compile(self) -> None:
        article = {
            "sample_id": "N1", "source_id": "source",
            "source_timestamp": "2026-01-01T12:00:00Z",
            "source_text_sha256": "f" * 64,
            "rendered_product": {"text": "ABC issued guidance."},
            "point_in_time_issuer_candidates": [{
                "entity_id": "security:ABC", "entity_kind": "security",
                "display_name": "ABC", "ticker": "ABC",
                "identity_status": "resolved", "identity_evidence": ["ABC"],
            }],
        }
        spec = {
            "sample_id": "N1", "review_notes": "Reviewed.",
            "envelope": {
                "document_structure": "single_subject",
                "communication_purpose": "report",
                "information_origin": "issuer",
                "production_method": "original",
                "text_availability": "rendered",
            },
            "entities": [{"ticker": "ABC"}],
            "statements": [{
                "statement_kind": "event", "concept_leaf": "guidance.issued",
                "epistemic_status": "confirmed", "time_relation": "current",
                "evidence": ["ABC issued guidance."],
                "participations": [{
                    "entity_id": "security:ABC", "semantic_role": "affected_subject",
                    "discourse_role": "none", "semantic_sentiment": "neutral",
                    "sentiment_strength": 0,
                }],
            }],
        }
        document = compile_review_spec(article, spec)
        self.assertEqual(document["envelope"]["information_origin"]["value"], "issuer")
        self.assertEqual(document["envelope"]["information_origin"]["evidence"], [])

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

if __name__ == "__main__":
    unittest.main()
