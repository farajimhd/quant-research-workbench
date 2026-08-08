from __future__ import annotations

import unittest

from .sol_teacher_forecast_gold_review import render_gold_review_packet


class SolTeacherForecastGoldReviewTests(unittest.TestCase):
    def test_packet_is_prediction_blind_and_contains_complete_source(self) -> None:
        article = {
            "sample_id": "S00001",
            "source_id": "source-1",
            "source_timestamp": "2026-01-01T12:00:00Z",
            "source_text_sha256": "a" * 64,
            "publication": {
                "title": "Alpha raises guidance",
                "provider": "benzinga",
                "provider_tickers": ["AAA"],
            },
            "rendered_product": {"text": "Alpha <raises> guidance & outlook."},
        }
        document = {
            "sample_id": "S00001",
            "entities": [{
                "entity_id": "issuer:aaa",
                "ticker": "AAA",
                "display_name": "Alpha",
                "identity_evidence": ["ticker:AAA"],
            }],
            "statements": [{
                "statement_id": "S1",
                "concept_leaf": "guidance.issued",
                "statement_kind": "forecast",
                "time_relation": "forward",
                "evidence_spans": [],
            }],
            "issuer_views": [{
                "entity_id": "issuer:aaa",
                "statement_ids": ["S1"],
                "composite_sentiment": "positive",
                "positive_strength": 2,
                "negative_strength": 0,
            }],
            "migration": {"status": "review_required"},
        }
        unit = {
            "unit_id": "S00001::AAA",
            "sample_id": "S00001",
            "ticker": "AAA",
            "concepts": ["guidance.issued"],
        }

        packet = render_gold_review_packet(article, document, unit)

        self.assertIn("Alpha &lt;raises&gt; guidance &amp; outlook.", packet)
        self.assertIn('"sol_derived_direction": "positive"', packet)
        self.assertNotIn("predicted_sentiment", packet)
        self.assertNotIn("exact_direction", packet)
        self.assertNotIn("engine_version", packet)


if __name__ == "__main__":
    unittest.main()
