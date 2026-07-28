from __future__ import annotations

import unittest

from .labeler import label_document
from .schema import SemanticDocument


def document(text: str, *, corpus: str = "news", title: str = "") -> SemanticDocument:
    return SemanticDocument(
        corpus=corpus,
        source_id="case",
        timestamp="2026-07-28T12:00:00Z",
        title=title,
        text=text,
        entity_terms=("Example Therapeutics, Inc.",),
        tickers=("EXMP",),
    )


class TypedSpanTests(unittest.TestCase):
    def test_dates_times_tickers_and_identifiers_are_not_fragmented(self) -> None:
        result = label_document(document(
            "Example Therapeutics, Inc. (NASDAQ:EXMP) filed Form X-17A-5 "
            "on 2025-11-05 at 09:30 ET. CIK: 0001234567; "
            "accession 0001234567-25-000321; EIN 12-3456789."
        ))
        pairs = {(span.span_type, span.subtype, span.raw) for span in result.spans}
        self.assertIn(("market_identity", "exchange_ticker", "(NASDAQ:EXMP)"), pairs)
        form = next(span for span in result.spans if span.subtype == "sec_form")
        self.assertEqual(form.normalized, "FORM X-17A-5")
        self.assertIn(("temporal", "iso_date", "2025-11-05"), pairs)
        self.assertIn(("temporal", "clock_time", "09:30 ET"), pairs)
        self.assertIn(("identifier", "sec_accession", "0001234567-25-000321"), pairs)
        self.assertFalse(any(span.subtype == "number" and span.raw in {"17", "5", "2025", "11", "05"} for span in result.spans))

    def test_currency_types_and_compact_magnitudes(self) -> None:
        result = label_document(document(
            "The issuer sold 7.4 million shares at $3.60 per share and raised CA$4.5M."
        ))
        by_subtype = {}
        for span in result.spans:
            by_subtype.setdefault(span.subtype, []).append(span)
        self.assertEqual(by_subtype["share_count"][0].normalized, "7400000")
        self.assertEqual(by_subtype["price_per_share"][0].unit, "USD/share")
        self.assertEqual(by_subtype["money"][0].normalized, "4500000")
        self.assertEqual(by_subtype["money"][0].unit, "CAD")

    def test_financial_ranges_and_dotted_named_dates(self) -> None:
        result = label_document(document(
            "On Feb. 13, 2025 the issuer reaffirmed revenue guidance of $130-140 million."
        ))
        date = next(span for span in result.spans if span.subtype == "named_date")
        value = next(span for span in result.spans if span.subtype == "money_range")
        self.assertEqual(date.raw, "Feb. 13, 2025")
        self.assertEqual(value.normalized, "130000000..140000000")
        self.assertEqual(value.attributes["lower"], "130000000")
        self.assertEqual(value.attributes["upper"], "140000000")

    def test_form_words_and_regulation_names_are_not_false_sec_forms(self) -> None:
        result = label_document(document(
            "Complete any such form with the Commission under Regulation S-T; "
            "then submit Form ID and Forms 3, 4, and 5.",
            corpus="sec",
        ))
        raw_forms = {span.raw for span in result.spans if span.subtype == "sec_form"}
        self.assertNotIn("form with", raw_forms)
        self.assertNotIn("S-T", raw_forms)
        self.assertTrue(any(span.subtype == "edgar_form_id" for span in result.spans))
        self.assertTrue(any(span.subtype == "sec_form_list" for span in result.spans))

    def test_standalone_year_and_sec_postal_code_are_typed(self) -> None:
        result = label_document(document(
            "Washington, D.C. 20549. Revenue increased in 2021.",
            corpus="sec",
        ))
        self.assertTrue(any(span.subtype == "postal_code" for span in result.spans))
        self.assertTrue(any(span.subtype == "year" and span.raw == "2021" for span in result.spans))
        self.assertFalse(any(span.subtype == "number" and span.raw in {"20549", "2021"} for span in result.spans))

    def test_table_scale_is_inherited_by_unadorned_values(self) -> None:
        result = label_document(document(
            "Table: Balance Sheet ($000's)\n"
            "Columns: Metric; 2025\n"
            "Metric=Cash | 63,715\n",
            corpus="sec",
        ))
        values = [span for span in result.spans if span.raw == "63,715"]
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].subtype, "table_quantity")
        self.assertEqual(values[0].normalized, "63715000")
        self.assertEqual(values[0].unit, "USD")


class StructureAndLabelTests(unittest.TestCase):
    def test_provenance_contact_boilerplate_and_duplicate_are_suppressed(self) -> None:
        paragraph = (
            "The company announced a definitive merger agreement that remains "
            "subject to customary closing conditions and shareholder approval."
        )
        result = label_document(document(
            "Source [provider_body:0] https://example.test\n"
            f"{paragraph}\n{paragraph}\n"
            "Investor Relations: person@example.com\n"
            "Forward-looking statements are subject to risks."
        ))
        kinds = {block.kind for block in result.blocks if not block.semantic}
        self.assertTrue({"renderer_provenance", "duplicate", "contact", "boilerplate"} <= kinds)
        self.assertEqual(result.normalized_semantic_text.count("definitive merger agreement"), 1)

    def test_truncated_teaser_prefix_is_suppressed(self) -> None:
        result = label_document(document(
            "Teaser: Issuer announced a registered direct offering for gross proceeds\n"
            "Issuer announced a registered direct offering for gross proceeds of $25 million."
        ))
        self.assertTrue(any(block.kind == "duplicate_teaser" for block in result.blocks))
        self.assertEqual(result.normalized_semantic_text.count("registered direct offering"), 1)

    def test_canonical_labels_have_exact_evidence(self) -> None:
        source = (
            "The company raised guidance after earnings beat expectations, "
            "but also announced a registered direct offering."
        )
        result = label_document(document(source))
        labels = {(label.family, label.subtype): label for label in result.labels}
        self.assertIn(("guidance", "raise"), labels)
        self.assertIn(("earnings", "beat"), labels)
        self.assertIn(("financing", "registered_direct"), labels)
        # Opposing evidence is retained component-wise; two stronger positive
        # events outweigh one negative financing event in the deterministic score.
        self.assertEqual(result.sentiment, "positive")
        for label in result.labels:
            self.assertTrue(label.evidence)
            for evidence in label.evidence:
                self.assertEqual(source[evidence.start:evidence.end], evidence.text)

    def test_roundup_role_is_not_primary_event(self) -> None:
        result = label_document(document(
            "50 Biggest Movers From Friday includes EXMP and several other stocks.",
            title="50 Biggest Movers From Friday",
        ))
        self.assertEqual(result.content_role, "market_roundup")
        self.assertEqual(result.origin, "editorial_aggregation")
        self.assertNotIn("no_supported_canonical_event", result.quality_flags)

    def test_flexible_board_appointment_has_exact_evidence(self) -> None:
        result = label_document(document(
            "Issuer announced today that Jane Doe has been elected to Issuer's board of directors.",
            corpus="sec",
        ))
        label = next(
            value for value in result.labels
            if value.subtype == "board_appointment"
        )
        self.assertIn("elected to Issuer's board of directors", label.evidence[0].text)
        self.assertEqual(result.content_role, "primary_event")

    def test_candidate_phrases_do_not_create_labels(self) -> None:
        result = label_document(document(
            "Unusual speculative language appears repeatedly. "
            "Unusual speculative language appears repeatedly."
        ))
        self.assertTrue(result.candidates)
        self.assertFalse(result.labels)
        self.assertIn("no_supported_canonical_event", result.quality_flags)

    def test_candidate_evidence_marks_curated_concept_without_becoming_authority(self) -> None:
        result = label_document(document("The issuer announced a registered direct offering."))
        candidate = next(value for value in result.candidates if value.phrase == "registered direct offering")
        self.assertEqual(candidate.seed_concept, "financing.registered_direct")
        label = next(value for value in result.labels if value.subtype == "registered_direct")
        self.assertEqual(label.evidence[0].text.casefold(), "registered direct offering")
if __name__ == "__main__":
    unittest.main()
