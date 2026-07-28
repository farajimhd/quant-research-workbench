from __future__ import annotations

import unittest

from .audit_samples import AuditCase, document_from_case, render_case
from .config import CandidateInventoryConfig
from .mining import CandidateAccumulator, SourceDocument, mining_text
from .normalize import candidate_ngrams, normalize_financial_text
from .pipeline import DocumentBudget, config_fingerprint
from .sources import WorkUnit, month_windows, news_page_sql, sec_page_sql


class NormalizeTests(unittest.TestCase):
    def test_financial_values_keep_type_number_and_context(self) -> None:
        result = normalize_financial_text(
            "Issuer sold 7.4 million shares at $3.60 per share for $25 million, or 4.99%.",
            entity_terms=("Issuer",),
        )
        self.assertIn("<share_count>", result.text)
        self.assertIn("<price_per_share>", result.text)
        self.assertIn("<money>", result.text)
        self.assertIn("<percentage>", result.text)
        by_type = {row.value_type: row for row in result.values}
        self.assertEqual(by_type["share_count"].normalized_number, "7400000")
        self.assertEqual(by_type["price_per_share"].normalized_number, "3.6")
        self.assertEqual(by_type["money"].normalized_number, "25000000")
        self.assertEqual(by_type["percentage"].normalized_number, "4.99")
        self.assertIn("sold", by_type["share_count"].context)

    def test_compact_magnitude_suffixes_are_scaled(self) -> None:
        result = normalize_financial_text(
            "The issuer raised $4.5M and sold 250K shares in a $1.2B program."
        )
        money = [
            row.normalized_number
            for row in result.values
            if row.value_type == "money"
        ]
        shares = [
            row.normalized_number
            for row in result.values
            if row.value_type == "share_count"
        ]
        self.assertEqual(money, ["4500000", "1200000000"])
        self.assertEqual(shares, ["250000"])

    def test_specialized_and_fallback_numbers_do_not_fragment_phrases(self) -> None:
        result = normalize_financial_text(
            "Margin rose 25 basis points to 18.4%, leverage was 2.5x, and 17 sites opened."
        )
        self.assertIn("<basis_points>", result.text)
        self.assertIn("<percentage>", result.text)
        self.assertIn("<multiple>", result.text)
        self.assertIn("<number>", result.text)
        self.assertNotIn("25 basis", result.text)

    def test_ngram_boundaries_remove_stop_word_edges(self) -> None:
        phrases = {
            phrase
            for phrase, _ in candidate_ngrams(
                "company entered into a registered direct offering",
                min_ngram=2,
                max_ngram=4,
            )
        }
        self.assertIn("registered direct offering", phrases)
        self.assertNotIn("into a", phrases)


class MiningTests(unittest.TestCase):
    def accumulator(self, *, maximum: int = 1000) -> CandidateAccumulator:
        return CandidateAccumulator(
            corpus="news",
            capacity=1000,
            example_limit=3,
            evidence_chars=120,
            min_ngram=2,
            max_ngram=5,
            max_unique_per_document=maximum,
        )

    def test_document_presence_and_seed_concept(self) -> None:
        accumulator = self.accumulator()
        accumulator.add_document(
            SourceDocument(
                corpus="news",
                source_id="n1",
                timestamp="2025-03-01T13:00:00+00:00",
                title="Company Prices Registered Direct Offering",
                text=(
                    "The company announced a registered direct offering. "
                    "The registered direct offering has gross proceeds of $25 million."
                ),
            )
        )
        entry = accumulator.candidates["registered direct offering"]
        self.assertEqual(entry.document_count, 1)
        self.assertGreaterEqual(entry.occurrence_count, 2)
        self.assertEqual(entry.concept, "registered_direct_offering")
        self.assertEqual(accumulator.values["money"].document_count, 1)

    def test_explicit_document_bound_is_visible(self) -> None:
        accumulator = self.accumulator(maximum=2)
        accumulator.add_document(
            SourceDocument(
                corpus="news",
                source_id="n2",
                timestamp="2025-03-01",
                title="",
                text="alpha beta gamma delta epsilon zeta",
            )
        )
        self.assertEqual(accumulator.counters.candidate_truncated_documents, 1)
        self.assertEqual(len(accumulator.candidates), 2)

    def test_merge_preserves_disjoint_document_support(self) -> None:
        left = self.accumulator()
        right = self.accumulator()
        for source_id, accumulator in (("left", left), ("right", right)):
            accumulator.add_document(
                SourceDocument(
                    corpus="news",
                    source_id=source_id,
                    timestamp="2025-03-01",
                    title="Contract Award",
                    text="The company received a contract award.",
                )
            )
        left.merge(right)
        self.assertEqual(left.candidates["contract award"].document_count, 2)
        self.assertEqual(left.counters.documents, 2)

    def test_token_error_bound_is_not_double_counted_on_merge(self) -> None:
        left = self.accumulator()
        right = self.accumulator()
        right._add_token("financing", 7, error_bound=3)
        left.merge(right)
        self.assertEqual(left.tokens["financing"].document_count, 7)
        self.assertEqual(left.tokens["financing"].error_bound, 3)

    def test_news_renderer_provenance_is_not_mined_as_article_language(self) -> None:
        document = SourceDocument(
            corpus="news",
            source_id="n3",
            timestamp="2025-01-01",
            title="Issuer Raises Guidance",
            text=(
                "Title: Issuer Raises Guidance\n"
                "Source [provider_body:0] https://example.test/story\n"
                "Revenue guidance increased.\n"
                "Image: src=https://example.test/pixel.gif\n"
                "Source [external_html:1] https://example.test/source\n"
                "The outlook remains constructive."
            ),
        )
        self.assertEqual(
            mining_text(document),
            "Revenue guidance increased.\nThe outlook remains constructive.",
        )


class SourceSqlTests(unittest.TestCase):
    def test_month_windows_respect_partial_boundaries(self) -> None:
        self.assertEqual(
            list(month_windows("2025-01-15", "2025-03-02")),
            [
                ("2025-01-15", "2025-02-01"),
                ("2025-02-01", "2025-03-01"),
                ("2025-03-01", "2025-03-02"),
            ],
        )

    def test_news_query_uses_current_v2_identity_and_keyset_cursor(self) -> None:
        sql = news_page_sql(
            CandidateInventoryConfig(sources=("news",)),
            WorkUnit(
                corpus="news",
                key="news-2025-01",
                partition_value=202501,
                start_date="2025-01-01",
                end_date_exclusive="2025-02-01",
            ),
            ("2025-01-12", "provider-9"),
        )
        self.assertIn("benzinga_news_rendered_v2", sql)
        self.assertIn("source_revision_key=e.source_revision_key", sql)
        self.assertIn("tuple(e.published_date, e.provider_article_id)", sql)
        self.assertIn("'provider-9'", sql)

    def test_sec_query_prunes_hash_lane_and_uses_primary_key_cursor(self) -> None:
        sql = sec_page_sql(
            CandidateInventoryConfig(sources=("sec",)),
            WorkUnit(corpus="sec", key="sec-lane-07", partition_value=7),
            ("0000320193", "0000320193-25-000001", "doc-1", "packed"),
        )
        self.assertIn("cityHash64(cik) % 64 = 7", sql)
        self.assertIn("tuple(cik, accession_number, document_id, text_kind)", sql)
        self.assertIn("sec_filing_text_rendered_v3", sql)
        self.assertIn("sec_filing_document_v3", sql)
        self.assertIn("sec_filing_v3", sql)
        self.assertIn("f.company_name", sql)
        self.assertIn("source_archive_date >= toDate('2010-01-01')", sql)
        self.assertIn("source_archive_date < toDate('2027-01-01')", sql)


class ConfigTests(unittest.TestCase):
    def test_validation_rejects_unbounded_ngram_width(self) -> None:
        with self.assertRaisesRegex(ValueError, "ngram bounds"):
            CandidateInventoryConfig(max_ngram=9).validate()

    def test_run_root_separates_sources_and_bounded_validation(self) -> None:
        full = CandidateInventoryConfig(sources=("news",))
        bounded = CandidateInventoryConfig(
            sources=("news",),
            max_documents_per_source=20,
        )
        self.assertNotEqual(full.run_root, bounded.run_root)
        self.assertIn("news_", full.run_root.name)
        self.assertIn("limit-20", bounded.run_root.name)

    def test_execution_tuning_does_not_invalidate_checkpoint_contract(self) -> None:
        first = CandidateInventoryConfig(workers=2, news_page_size=64)
        second = CandidateInventoryConfig(workers=32, news_page_size=512)
        self.assertEqual(config_fingerprint(first), config_fingerprint(second))

    def test_document_budget_can_resume_from_checkpoint_usage(self) -> None:
        budget = DocumentBudget(20, initial_used={"news": 17})
        self.assertEqual(budget.take("news", 10), 3)
        self.assertTrue(budget.exhausted("news"))


class AuditReportTests(unittest.TestCase):
    def test_news_report_separates_current_classification_from_candidates(self) -> None:
        case = AuditCase(
            corpus="news",
            stratum="financing_offering",
            rationale="Financing language",
            row={
                "source_id": "news-1",
                "source_timestamp": "2025-01-02T13:15:00+00:00",
                "title": "Issuer Announces Registered Direct Offering",
                "text": (
                    "Title: Issuer Announces Registered Direct Offering\n"
                    "Source [provider_body:0] https://example.test/story\n"
                    "Issuer announced today a registered direct offering of "
                    "7.4 million shares at $3.60 per share."
                ),
                "entity_terms": ["ISSR", "Issuer"],
                "tickers": ["ISSR"],
                "channels": ["Offerings"],
                "provider_tags": [],
                "links": ["https://www.globenewswire.com/release"],
                "author": "Benzinga Newsdesk",
            },
        )
        report = render_case(case, document_from_case(case))
        self.assertIn('"authority": "news_rules_v1"', report)
        self.assertIn('"status": "current_production_rule_output"', report)
        self.assertIn("## Stage 2 — typed values", report)
        self.assertIn("## Method audit observations", report)
        self.assertIn("`share_count`", report)
        self.assertIn("registered direct offering", report)
        self.assertIn("## Original rendered input", report)
        self.assertIn("## Semantic text used for mining", report)

    def test_sec_report_does_not_fabricate_semantic_classification(self) -> None:
        case = AuditCase(
            corpus="sec",
            stratum="prospectus",
            rationale="Prospectus",
            row={
                "source_id": "sec-1",
                "source_timestamp": "2025-01-02T13:15:00+00:00",
                "title": "424B5 Prospectus",
                "text": "PROSPECTUS\nThe registrant may offer $25 million of common stock.",
                "entity_terms": ["0000000001", "Issuer"],
                "text_kind": "prospectus",
                "form_type": "424B5",
            },
        )
        report = render_case(case, document_from_case(case))
        self.assertIn('"status": "not_implemented"', report)
        self.assertIn('"semantic_label_emitted": false', report)
        self.assertIn("does not invent one", report)
        self.assertIn("No SEC semantic class is emitted", report)
        self.assertIn("`money`", report)


if __name__ == "__main__":
    unittest.main()
