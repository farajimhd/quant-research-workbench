from __future__ import annotations

import unittest

from research.text_intelligence.semantic_label_authority_v1.schema import SemanticDocument

from .authority import classify_document


class ClassificationAuthorityTests(unittest.TestCase):
    def test_news_path_is_explicitly_retired(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="news-1",
            timestamp="2026-08-06T12:00:00Z",
            title="Example",
            text="Example Corp announced a contract.",
            tickers=("EXMP",),
        )

        with self.assertRaisesRegex(RuntimeError, "News path is retired"):
            classify_document(document)

    def test_sec_source_type_remains_independently_supported(self) -> None:
        result = classify_document(
            SemanticDocument(
                corpus="sec",
                source_id="doc-1",
                timestamp="2026-07-28T12:00:00Z",
                title="Form 8-K",
                text="The registrant announced a registered direct offering.",
                metadata={
                    "form_type": "8-K",
                    "document_type": "EX-99.1",
                    "document_role": "press_release_exhibit",
                    "text_kind": "press_release_exhibit",
                    "accepted_at_utc": "2026-07-28T12:00:00Z",
                },
            )
        )

        self.assertEqual(result.source_type, "8-K")
        self.assertEqual(result.source_origin, "regulatory_primary")
        self.assertTrue(result.reaction_evaluation_eligible)

if __name__ == "__main__":
    unittest.main()
