from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.backend.app import app, trading_canvas_context
from src.backend.qmd_gateway_client import QmdServiceError
from src.backend.response_contract import error_response_envelope, success_response_envelope
from src.request_context import begin_request_context, end_request_context


class ResponseContractTests(unittest.TestCase):
    def test_application_negotiates_success_envelope_without_changing_default(self) -> None:
        with TestClient(app) as client:
            default_response = client.get("/api/health")
            negotiated = client.get(
                "/api/health",
                headers={
                    "X-Correlation-ID": "web:health-1",
                    "X-Causation-ID": "view:readiness",
                    "X-Response-Envelope": "1",
                },
            )
        self.assertNotIn("X-Response-Envelope", default_response.headers)
        self.assertEqual(negotiated.headers["X-Response-Envelope"], "1")
        payload = negotiated.json()
        self.assertEqual(payload["data"], default_response.json())
        self.assertTrue(payload["complete"])
        self.assertEqual(payload["meta"]["correlation_id"], "web:health-1")
        self.assertEqual(payload["meta"]["causation_id"], "view:readiness")

    def test_success_envelope_promotes_existing_coverage_evidence(self) -> None:
        data = {"complete": False, "warnings": [{"code": "partial"}], "rows": []}
        payload = success_response_envelope(
            data,
            correlation_id="run:scanner",
            causation_id="snapshot:17",
        )
        self.assertFalse(payload["complete"])
        self.assertEqual(payload["warnings"], data["warnings"])
        self.assertIs(payload["data"], data)

    def test_application_http_errors_use_the_shared_envelope(self) -> None:
        with TestClient(app) as client:
            response = client.get(
                "/api/definitely-missing",
                headers={
                    "X-Correlation-ID": "web:test-typed-error",
                    "X-Causation-ID": "route:missing",
                },
            )
        payload = response.json()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "not_found")
        self.assertEqual(payload["error"]["correlation_id"], "web:test-typed-error")
        self.assertEqual(payload["error"]["causation_id"], "route:missing")
        self.assertEqual(payload["detail"], payload["error"]["message"])
        self.assertEqual(response.headers["X-Correlation-ID"], "web:test-typed-error")

    def test_string_detail_keeps_compatibility_and_adds_typed_identity(self) -> None:
        correlation, causation, _, _ = begin_request_context(
            "web:request-31", "command:publish-4"
        )
        try:
            payload = error_response_envelope(
                status_code=404,
                detail="Run not found",
            )
        finally:
            end_request_context(correlation, causation)
        self.assertEqual(payload["detail"], "Run not found")
        self.assertFalse(payload["complete"])
        self.assertIsNone(payload["data"])
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["error"]["code"], "not_found")
        self.assertEqual(payload["error"]["correlation_id"], "web:request-31")
        self.assertEqual(payload["error"]["causation_id"], "command:publish-4")
        self.assertFalse(payload["error"]["retryable"])

    def test_structured_upstream_error_preserves_detail_and_retryability(self) -> None:
        detail = {
            "code": "qmd_upstream_unavailable",
            "message": "QMD is unavailable",
            "retryable": True,
            "service": "QMD",
        }
        payload = error_response_envelope(status_code=503, detail=detail)
        self.assertEqual(payload["detail"], detail)
        self.assertEqual(payload["error"]["code"], "qmd_upstream_unavailable")
        self.assertEqual(payload["error"]["message"], "QMD is unavailable")
        self.assertTrue(payload["error"]["retryable"])
        self.assertEqual(payload["error"]["details"]["service"], "QMD")

    @patch("src.backend.app.historical_latest_coverage")
    def test_canvas_context_reports_qmd_timeout_as_retryable_gateway_timeout(
        self, latest_coverage
    ) -> None:
        latest_coverage.side_effect = QmdServiceError(
            service="QMD History",
            operation="GET",
            path="/coverage/latest",
            code="qmd_upstream_timeout",
            message="QMD History GET /coverage/latest timed out after 15 seconds.",
            retryable=True,
        )

        with self.assertRaises(HTTPException) as raised:
            trading_canvas_context()

        self.assertEqual(raised.exception.status_code, 504)
        self.assertEqual(raised.exception.detail["code"], "qmd_upstream_timeout")
        self.assertTrue(raised.exception.detail["retryable"])
        self.assertEqual(raised.exception.detail["service"], "QMD History")

    @patch(
        "src.backend.app.historical_latest_coverage",
        return_value={"session_date": "2026-08-17", "event_count": 10, "ticker_count": 2},
    )
    def test_canvas_context_reuses_one_short_lived_coverage_snapshot(
        self, latest_coverage
    ) -> None:
        first = trading_canvas_context()
        second = trading_canvas_context()

        self.assertEqual(first["coverage"]["ticker_count"], 2)
        self.assertEqual(second["coverage"]["ticker_count"], 2)
        latest_coverage.assert_called_once_with()

    def test_validation_list_has_stable_code_and_message(self) -> None:
        issues = [{"loc": ["query", "limit"], "msg": "must be positive"}]
        payload = error_response_envelope(
            status_code=422,
            detail=issues,
            code="validation_failed",
        )
        self.assertEqual(payload["detail"], issues)
        self.assertEqual(payload["error"]["code"], "validation_failed")
        self.assertEqual(payload["error"]["message"], "must be positive")


if __name__ == "__main__":
    unittest.main()
