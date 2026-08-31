from __future__ import annotations

import hashlib
import unittest

from .text_contract import (
    BODY_TEXT_CONTRACT_VERSION,
    MODEL_TEXT_CONTRACT_VERSION,
    body_v3_source_row,
    compose_model_text,
)


class NewsTextContractTests(unittest.TestCase):
    def test_model_text_keeps_title_and_body_without_wrappers(self) -> None:
        text = compose_model_text("Issuer reports results", "Revenue increased 12%.")
        self.assertEqual(text, "Issuer reports results\n\nRevenue increased 12%.")
        self.assertNotIn("Title:", text)
        self.assertNotIn("Source [", text)

    def test_missing_body_does_not_become_canonical_body(self) -> None:
        row = body_v3_source_row({"title": "Issuer update", "canonical_body_text": "", "body_status": "missing"})
        self.assertEqual(row["canonical_body_text"], "")
        self.assertEqual(row["text"], "Issuer update")
        self.assertEqual(row["body_status"], "missing")
        self.assertEqual(row["body_text_contract"], BODY_TEXT_CONTRACT_VERSION)
        self.assertEqual(row["model_text_contract"], MODEL_TEXT_CONTRACT_VERSION)
        self.assertEqual(row["model_text_hash"], hashlib.sha256(b"Issuer update").hexdigest())


if __name__ == "__main__":
    unittest.main()
