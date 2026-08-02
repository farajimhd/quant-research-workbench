from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from pipelines.news.benzinga.core.clickhouse_values import datetime64_utc_text
from pipelines.news.benzinga.core.clickhouse_writer_v2 import NewsV2TargetConfig
from pipelines.news.benzinga.news_benzinga_render_v2 import build_v2_rows, render_news_article
from pipelines.news.benzinga.news_benzinga_rendered_v2_rebuild import day_is_complete


class StructuredNewsRendererTest(unittest.TestCase):
    def setUp(self) -> None:
        self.row = {
            "provider": "benzinga",
            "provider_article_id": "42",
            "canonical_news_id": "benzinga:42",
            "published_date": "2026-07-14",
            "published_at_utc": "2026-07-14 16:00:00.000000000",
            "published_raw": "2026-07-14T16:00:00Z",
            "last_updated_at_utc": None,
            "last_updated_raw": "",
            "downloaded_at_utc": "2026-07-14 16:00:01.000000000",
            "provider_delay_ns": 1_000_000_000,
            "title": "Issuer reports results",
            "normalized_title": "Issuer reports results",
            "teaser": "",
            "body_text": "",
            "external_text": "",
            "pdf_text": "",
            "article_url": "https://example.com/news/42",
            "url_domain": "example.com",
            "author": "Reporter",
            "tickers": ["AAA", "BBB"],
            "channels": ["earnings"],
            "provider_tags": ["results"],
            "image_urls": [],
            "links": [],
            "raw_artifact_path": "raw.json",
            "raw_payload_hash": "a" * 64,
            "content_quality_flags": [],
        }

    def test_tables_lists_and_images_remain_structured(self) -> None:
        payload = {
            "id": "42",
            "title": self.row["title"],
            "url": self.row["article_url"],
            "body": """
                <h2>Quarter</h2>
                <table><tr><th>Metric</th><th>Value</th></tr>
                <tr><td>Revenue</td><td>$12.4M</td></tr></table>
                <ul><li>Raised guidance</li><li>Reduced debt</li></ul>
                <img src="chart.png" alt="Revenue chart">
            """,
        }
        rendered = render_news_article(payload, normalized_row=self.row)
        self.assertIn("Metric=Revenue", rendered.packed_text)
        self.assertIn("Value=$12.4M", rendered.packed_text)
        self.assertIn("Raised guidance", rendered.packed_text)
        self.assertIn("Revenue chart", rendered.packed_text)
        kinds = {block.block_kind for block in rendered.blocks}
        self.assertIn("table_row", kinds)
        self.assertIn("image", kinds)

    def test_article_is_embedded_once_and_ticker_links_are_separate(self) -> None:
        payload = {"id": "42", "title": self.row["title"], "body": "<p>Body</p>"}
        rendered = render_news_article(payload, normalized_row=self.row)
        rows = build_v2_rows(payload, self.row, rendered)
        self.assertEqual(rows["rendered"]["canonical_news_id"], "benzinga:42")
        self.assertEqual([row["ticker"] for row in rows["tickers"]], ["AAA", "BBB"])
        self.assertEqual(len({row["rendered_text_hash"] for row in rows["tickers"]}), 1)

    def test_v2_generated_datetimes_use_clickhouse_native_text(self) -> None:
        moment = datetime(2026, 7, 27, 14, 47, 9, 586093, tzinfo=UTC)
        self.assertEqual(datetime64_utc_text(moment), "2026-07-27 14:47:09.586093")
        self.assertEqual(
            datetime64_utc_text("2026-07-27T14:47:09.586093+00:00"),
            "2026-07-27 14:47:09.586093",
        )
        payload = {"id": "42", "title": self.row["title"], "body": "<p>Body</p>"}
        rendered = render_news_article(payload, normalized_row=self.row)
        rows = build_v2_rows(payload, self.row, rendered, updated_at_utc=moment)
        generated = [
            rows["event"]["updated_at_utc"],
            rows["rendered"]["updated_at_utc"],
            *(row["updated_at_utc"] for row in rows["sources"]),
            *(row["updated_at_utc"] for row in rows["blocks"]),
            *(row["updated_at_utc"] for row in rows["tickers"]),
        ]
        self.assertTrue(generated)
        self.assertEqual(set(generated), {"2026-07-27 14:47:09.586093"})

    def test_clickhouse_datetime_rejects_naive_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            datetime64_utc_text(datetime(2026, 7, 27, 14, 47, 9))

    def test_common_utf8_windows_1252_mojibake_is_repaired(self) -> None:
        payload = {"id": "42", "title": "Issuerâ€™s update", "body": "<p>Managementâ€™s outlookâ€”raised.</p>"}
        rendered = render_news_article(payload, normalized_row=self.row)
        self.assertIn("Management's outlook-raised", rendered.packed_text)
        self.assertNotIn("â", rendered.packed_text)
        self.assertIn("mojibake_repaired", rendered.quality_flags)

    def test_transport_artifact_external_text_is_rejected(self) -> None:
        row = dict(self.row)
        row["external_text"] = (
            "As you were browsing something about your browser made us think "
            "you were a bot. Please stand by. We're getting everything ready."
        )
        payload = {
            "id": "42",
            "title": self.row["title"],
            "body": "<p>Authoritative provider body.</p>",
        }
        rendered = render_news_article(payload, normalized_row=row)
        self.assertIn("Authoritative provider body", rendered.packed_text)
        self.assertNotIn("made us think you were a bot", rendered.packed_text)
        self.assertIn(
            "external_transport_artifact_rejected", rendered.quality_flags
        )

    def test_javascript_gate_enrichment_is_rejected(self) -> None:
        payload = {
            "id": "42",
            "title": self.row["title"],
            "body": "<p>Authoritative provider body.</p>",
        }
        rendered = render_news_article(
            payload,
            normalized_row=self.row,
            enrichment_rows=({
                "extracted_text": "To use this website, please enable JavaScript.",
                "resolved_action": "fetch_html",
                "extracted_text_hash": "transport-hash",
            },),
        )
        self.assertNotIn("enable JavaScript", rendered.packed_text)
        self.assertIn(
            "external_transport_artifact_rejected", rendered.quality_flags
        )


    def test_resume_requires_every_daily_product_to_be_complete(self) -> None:
        class FakeClient:
            def __init__(self, values: list[int]) -> None:
                self.values = values

            def execute(self, _sql: str) -> str:
                return "\t".join(str(value) for value in self.values)

        complete = [12, 12, 0, 18, 18, 14, 14, 91, 91]
        kwargs = {
            "target": NewsV2TargetConfig(),
            "day": date(2026, 7, 14),
            "expected": 12,
            "source_database": "q_live",
            "source_table": "benzinga_news_normalized_v1",
        }
        self.assertTrue(day_is_complete(FakeClient(complete), **kwargs))
        incomplete_blocks = complete.copy()
        incomplete_blocks[-1] -= 1
        self.assertFalse(day_is_complete(FakeClient(incomplete_blocks), **kwargs))


if __name__ == "__main__":
    unittest.main()
