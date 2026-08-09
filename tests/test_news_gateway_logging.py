from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from services.news_gateway.gateway import LiveNewsPayload, NewsGateway
from services.news_gateway.run_logger import sanitize


class _RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def event(self, event: str, **payload: object) -> None:
        self.events.append((event, payload))


class _StubEnricher:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def enrich_tasks(self, _tasks: list[dict[str, object]]) -> SimpleNamespace:
        return SimpleNamespace(rows=self.rows)


def _live_item() -> LiveNewsPayload:
    result = SimpleNamespace(
        provider_article_id="provider-1",
        canonical_news_id="canonical-1",
        normalized_row={
            "published_at_utc": "2026-08-09 17:00:00.000000",
            "title": "private title",
            "teaser": "private teaser",
            "body_text": "private body",
            "normalized_full_text": "private normalized text",
            "article_url": "https://example.com/private-path",
            "requires_enrichment": True,
        },
        ticker_links=[{"ticker": "AAPL"}],
        url_resolution=SimpleNamespace(
            fetch_tasks=[{"normalized_url": "https://example.com/private-path"}],
            attachments=[],
        ),
    )
    return LiveNewsPayload(
        payload={"id": "provider-1", "title": "private provider title", "body": "private provider body"},
        raw_path="raw.json",
        raw_hash="hash",
        downloaded_at_utc=SimpleNamespace(),
        initial_item=SimpleNamespace(result=result),
    )


class NewsGatewayLoggingTests(unittest.TestCase):
    def test_successful_url_enrichment_is_not_logged_as_incomplete(self) -> None:
        gateway = NewsGateway.__new__(NewsGateway)
        gateway.enricher = _StubEnricher(
            [
                {"status": "success", "status_reason": "html_extracted", "http_status": 200},
                {"status": "extracted", "status_reason": "legacy_extracted", "http_status": 200},
            ]
        )
        gateway.logger = _RecordingLogger()

        rows = gateway._enrich_live_item(_live_item())

        self.assertEqual(len(rows), 2)
        self.assertEqual(gateway.logger.events, [])

    def test_failed_url_enrichment_logs_bounded_operational_metadata(self) -> None:
        gateway = NewsGateway.__new__(NewsGateway)
        gateway.enricher = _StubEnricher(
            [{"status": "failed", "status_reason": "fetch_or_extract_failed", "http_status": 403}]
        )
        gateway.logger = _RecordingLogger()

        gateway._enrich_live_item(_live_item())

        self.assertEqual(len(gateway.logger.events), 1)
        event, payload = gateway.logger.events[0]
        self.assertEqual(event, "live_url_enrichment_incomplete")
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn("private title", serialized)
        self.assertNotIn("private teaser", serialized)
        self.assertNotIn("private body", serialized)
        self.assertNotIn("private-path", serialized)
        self.assertIn("example.com", serialized)
        self.assertIn("canonical-1", serialized)
        self.assertLess(len(serialized), 5_000)

    def test_logger_sanitizer_drops_content_bearing_fields_recursively(self) -> None:
        sanitized = sanitize(
            {
                "canonical_news_id": "canonical-1",
                "nested": {
                    "title": "private title",
                    "body_text": "private body",
                    "provider_payload": {"body": "private provider body"},
                    "domain_sample": ["example.com"],
                },
            }
        )

        self.assertEqual(
            sanitized,
            {
                "canonical_news_id": "canonical-1",
                "nested": {"domain_sample": ["example.com"]},
            },
        )


if __name__ == "__main__":
    unittest.main()
