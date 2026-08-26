from __future__ import annotations

import json
import unittest

from src.backend.sec_synthesis_service import load_sec_synthesis_state


class SecSynthesisServiceTests(unittest.TestCase):
    def test_attaches_synthesis_and_manual_review(self) -> None:
        synthesis = {"accession_number": "0001", "contract_version": "sec_synthesis_v1"}

        def query_rows(sql: str) -> list[dict[str, object]]:
            if ".sec_synthesis_v1" in sql:
                self.assertIn("sec_synthesis_engine_v1", sql)
                return [{"accession_number": "0001", "synthesis_json": json.dumps(synthesis), "updated_at_utc": "2026-01-01"}]
            if ".sec_llm_issuer_review_v1" in sql:
                return [{
                    "accession_number": "0001", "status": "complete", "trigger_mode": "manual",
                    "requested_by": "operator", "review_json": json.dumps({"fundamental_direction": "positive"}),
                    "fundamental_direction": "positive", "materiality_probability": 0.8,
                    "forecast_relevance_probability": 0.7, "provider": "openai-deep", "model": "model",
                    "cost_usd": 0.01, "latency_ms": 100, "error": "", "updated_at_utc": "2026-01-01",
                }]
            raise AssertionError(sql)

        result = load_sec_synthesis_state(["0001"], database="q_live", query_rows=query_rows)["0001"]
        self.assertEqual(synthesis, result["synthesis"])
        self.assertEqual("manual", result["review"]["trigger_mode"])
        self.assertEqual("positive", result["review"]["result"]["fundamental_direction"])


if __name__ == "__main__":
    unittest.main()
