from __future__ import annotations

import unittest

from research.text_intelligence.news_synthesis_v1.engine import IssuerIdentity, IssuerIdentityIndex, NewsSynthesisEngine
from src.backend.news_synthesis import presentation_payload


class NewsSynthesisPresentationTests(unittest.TestCase):
    def test_v1_document_maps_to_canvas_contract(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((IssuerIdentity("AAA", "issuer:aaa", "Alpha Corp", ("Alpha Corp",)),)))
        document = engine.synthesize({"source_id": "n1", "source_timestamp": "2026-08-03T12:00:00Z", "title": "Alpha wins contract", "text": "Alpha Corp (NASDAQ:AAA) was awarded a contract.", "tickers": ["AAA"]})
        payload = presentation_payload(document)
        self.assertEqual(payload["article_fields"]["news_kind"], "editorial")
        self.assertEqual(payload["labels"][0]["ticker"], "AAA")
        self.assertTrue(payload["summary"]["forecast_trigger_eligible"])
        self.assertIs(payload["document"], document)


if __name__ == "__main__":
    unittest.main()
