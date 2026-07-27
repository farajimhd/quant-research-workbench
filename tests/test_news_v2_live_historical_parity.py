from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from pipelines.news.benzinga.core.coverage_manifest import (
    CoverageManifestConfig,
    count_news_rows,
    load_non_empty_bucket_counts,
)
from pipelines.news.benzinga.news_pipeline.config import BenzingaPipelineConfig
from pipelines.news.benzinga.news_pipeline.enrichment import NewsEnrichmentConfig, NewsUrlEnricher
from pipelines.news.benzinga.news_pipeline.pipeline import BenzingaNewsPipeline
from pipelines.news.benzinga.news_benzinga_historical_gap_fill import build_v2_stage_plan


class NewsV2LiveHistoricalParityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = {
            "id": "parity-42",
            "published": "2026-07-14T13:30:00Z",
            "updated": "2026-07-14T13:30:00Z",
            "title": "Issuer reports results",
            "teaser": "Quarterly update",
            "body": "<p>Provider body.</p>",
            "url": "https://news.example.test/parity-42",
            "author": "Reporter",
            "stocks": [{"name": "AAA"}],
            "channels": [{"name": "earnings"}],
            "tags": [{"name": "results"}],
            "links": ["https://issuer.example.test/results"],
        }
        self.downloaded_at = datetime(2026, 7, 14, 13, 30, 1, tzinfo=UTC)
        self.pipeline = BenzingaNewsPipeline(BenzingaPipelineConfig())

    def _enricher(self) -> NewsUrlEnricher:
        def downloader(task, _args, _limiter, artifact_root):
            return {
                **task,
                "status": "downloaded",
                "resolved_action": "fetch_html",
                "artifact_path": str(artifact_root / "source.html.gz"),
                "artifact_compression": "gzip",
                "final_url": task["normalized_url"],
                "content_type": "text/html",
            }

        def extractor(download, _max_text, _max_pdf):
            return {
                **download,
                "status": "extracted",
                "extracted_text": "Metric Revenue Value $12.4M",
                "extracted_text_hash": "a" * 64,
                "extraction_method": "html",
            }

        return NewsUrlEnricher(
            NewsEnrichmentConfig(artifact_root=Path("Z:/durable/news")),
            downloader=downloader,
            extractor=extractor,
            artifact_reader=lambda _path, _compression: (
                b"<table><tr><th>Metric</th><th>Value</th></tr>"
                b"<tr><td>Revenue</td><td>$12.4M</td></tr></table>"
            ),
        )

    def test_live_two_phase_and_historical_shared_path_render_identically(self) -> None:
        enricher = self._enricher()
        initial = self.pipeline.process_payload(
            self.payload,
            raw_artifact_path="raw.json",
            raw_payload_hash="b" * 64,
            downloaded_at_utc=self.downloaded_at,
        )
        live_rows = enricher.enrich_tasks(initial.result.url_resolution.fetch_tasks)
        live = self.pipeline.process_payload(
            self.payload,
            raw_artifact_path="raw.json",
            raw_payload_hash="b" * 64,
            downloaded_at_utc=self.downloaded_at,
            enrichment_rows=live_rows.rows,
        )
        historical = self.pipeline.process_payload_enriched(
            self.payload,
            enricher=enricher,
            raw_artifact_path="raw.json",
            raw_payload_hash="b" * 64,
            downloaded_at_utc=self.downloaded_at,
        ).processed

        self.assertEqual(live.result.v2_rendered_row["rendered_text"], historical.result.v2_rendered_row["rendered_text"])
        self.assertEqual(live.result.v2_rendered_row["rendered_text_hash"], historical.result.v2_rendered_row["rendered_text_hash"])
        self.assertEqual(
            [(row["source_kind"], row["rendered_text"]) for row in live.result.v2_source_rows],
            [(row["source_kind"], row["rendered_text"]) for row in historical.result.v2_source_rows],
        )
        self.assertIn("Metric=Revenue", historical.result.v2_rendered_row["rendered_text"])
        self.assertIn("Value=$12.4M", historical.result.v2_rendered_row["rendered_text"])

    def test_failed_enrichment_is_visible_in_v2_event(self) -> None:
        def downloader(task, _args, _limiter, _artifact_root):
            return {
                **task,
                "status": "failed",
                "status_reason": "timeout",
                "error_type": "TimeoutError",
            }

        enriched = self.pipeline.process_payload_enriched(
            self.payload,
            enricher=NewsUrlEnricher(
                NewsEnrichmentConfig(artifact_root=Path("Z:/durable/news")),
                downloader=downloader,
            ),
            raw_artifact_path="raw.json",
            raw_payload_hash="b" * 64,
            downloaded_at_utc=self.downloaded_at,
        )
        self.assertIn("enrichment_incomplete", enriched.processed.result.v2_event_row["content_quality_flags"])
        self.assertIn("enrichment_incomplete", enriched.processed.result.warnings)

    def test_coverage_queries_use_v2_event_authority_with_final(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def execute(self, query: str) -> str:
                self.queries.append(query)
                return "0" if query.startswith("SELECT count()") else ""

        client = FakeClient()
        config = CoverageManifestConfig(database="q_live", event_table="benzinga_news_event_v2")
        start = datetime(2026, 7, 14, tzinfo=UTC)
        end = datetime(2026, 7, 15, tzinfo=UTC)
        count_news_rows(client, config, start, end)
        load_non_empty_bucket_counts(client, config, start, end, 300)
        self.assertEqual(len(client.queries), 2)
        for query in client.queries:
            self.assertIn("benzinga_news_event_v2", query)
            self.assertIn(" FINAL ", query)
            self.assertNotIn("benzinga_news_normalized_v1", query)

    def test_legacy_historical_entrypoint_redirects_to_v2_package(self) -> None:
        plan = build_v2_stage_plan(
            SimpleNamespace(
                raw_root_win="D:/market-data/news-benzinga",
                prepared_root_win="D:/TradingML/runtimes/news",
                url_download_artifact_root_win="D:/market-data/news-benzinga/url-artifacts",
                normalizer_processes=16,
                text_limit_chars=50_000,
                execute_db=True,
                start_utc="2026-07-01T00:00:00Z",
                end_utc="2026-07-02T00:00:00Z",
                download_processes=16,
            )
        )
        commands = [" ".join(command) for _stage, command in plan]
        self.assertEqual([stage for stage, _command in plan], ["raw_download", "v2_package_gap_fill"])
        self.assertTrue(any("news_benzinga_package_gap_fill" in command for command in commands))
        self.assertTrue(any("--execute" in command for command in commands))
        self.assertFalse(any("build_normalized_rows" in command for command in commands))
        self.assertFalse(any("clickhouse_file_ingest" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
