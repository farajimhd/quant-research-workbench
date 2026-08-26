from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from src.backend.sec_synthesis_service import load_sec_synthesis_state, request_sec_review


class SecSynthesisServiceTests(unittest.TestCase):
    def test_sec_review_admission_uses_configured_bounded_timeout(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"status":"queued"}'
        environment = {
            "TEXT_INTELLIGENCE_URL": "http://text-intelligence.test:8804/",
            "TEXT_INTELLIGENCE_SEC_REVIEW_ADMISSION_TIMEOUT_SECONDS": "30",
        }

        with mock.patch.dict(os.environ, environment, clear=False), mock.patch(
            "src.backend.sec_synthesis_service.urllib.request.urlopen", return_value=response
        ) as urlopen:
            result = request_sec_review("0001930510", "0001213900-26-092120", "frontend-operator")

        self.assertEqual({"status": "queued"}, result)
        request = urlopen.call_args.args[0]
        self.assertEqual("http://text-intelligence.test:8804/sec-review", request.full_url)
        self.assertEqual(30.0, urlopen.call_args.kwargs["timeout"])
        self.assertEqual(
            {
                "cik": "0001930510",
                "accession_number": "0001213900-26-092120",
                "requested_by": "frontend-operator",
            },
            json.loads(request.data.decode()),
        )

    def test_sec_review_admission_rejects_nonpositive_timeout(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"TEXT_INTELLIGENCE_SEC_REVIEW_ADMISSION_TIMEOUT_SECONDS": "0"},
            clear=False,
        ), mock.patch("src.backend.sec_synthesis_service.urllib.request.urlopen") as urlopen:
            with self.assertRaisesRegex(ValueError, "must be positive"):
                request_sec_review("0001930510", "0001213900-26-092120", "operator")
        urlopen.assert_not_called()

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
