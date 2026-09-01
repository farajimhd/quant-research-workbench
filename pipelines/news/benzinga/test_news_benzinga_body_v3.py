from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from pipelines.news.benzinga.news_benzinga_body_v3 import (
    BODY_TEXT_CONTRACT,
    body_purity_reasons,
    build_body_v3_rows,
    render_canonical_body,
)
from pipelines.news.benzinga.news_pipeline.config import BenzingaPipelineConfig
from pipelines.news.benzinga.news_pipeline.pipeline import body_v4_shadow_state
from pipelines.news.benzinga.news_benzinga_body_v3_rebuild import parse_json_each_rows


class BodyOnlyNewsRendererTest(unittest.TestCase):
    def setUp(self) -> None:
        self.row = {
            "provider": "benzinga",
            "provider_article_id": "42",
            "canonical_news_id": "benzinga:42",
            "published_date": "2026-08-30",
            "published_at_utc": "2026-08-30 16:00:00.000000000",
            "published_raw": "2026-08-30T16:00:00Z",
            "last_updated_at_utc": None,
            "last_updated_raw": "",
            "downloaded_at_utc": "2026-08-30 16:00:01.000000000",
            "provider_delay_ns": 1_000_000_000,
            "title": "Issuer Reports Record Quarterly Revenue",
            "normalized_title": "issuer reports record quarterly revenue",
            "teaser": "",
            "body_text": "",
            "external_text": "",
            "pdf_text": "",
            "article_url": "https://example.com/news/42",
            "url_domain": "example.com",
            "author": "Reporter",
            "tickers": ["AAA"],
            "channels": ["earnings"],
            "provider_tags": ["results"],
            "image_urls": [],
            "links": [],
            "raw_artifact_path": "raw.json",
            "raw_payload_hash": "a" * 64,
            "content_quality_flags": [],
        }

    def test_inline_related_content_is_removed_without_losing_following_body(self) -> None:
        payload = {
            "title": self.row["title"],
            "body": """
                <p>Issuer shares rose after record revenue and stronger product demand.</p>
                <p>Read Also:</p>
                <p>Other Company Sees AI Drug Discovery Pipeline Expand</p>
                <p>Sales reached $261.30 million, ahead of the analyst consensus.</p>
                <p>Image via Shutterstock</p>
            """,
        }
        body = render_canonical_body(payload, normalized_row=self.row)
        self.assertIn("Issuer shares rose", body.canonical_body_text)
        self.assertIn("Sales reached $261.30 million", body.canonical_body_text)
        self.assertNotIn("Read Also", body.canonical_body_text)
        self.assertNotIn("Other Company", body.canonical_body_text)
        self.assertNotIn("Shutterstock", body.canonical_body_text)
        self.assertEqual(body.primary_source_kind, "provider_body")

    def test_external_source_is_supporting_when_provider_body_exists(self) -> None:
        payload = {
            "title": self.row["title"],
            "body": "<p>Issuer reported record quarterly revenue and raised its full-year outlook.</p>",
        }
        body = render_canonical_body(
            payload,
            normalized_row=self.row,
            enrichment_rows=[{
                "raw_html": "<html><body><nav>Markets News Stocks</nav><p>Unrelated external page content that is very long. " + "x " * 100 + "</p></body></html>",
                "extracted_text": "Unrelated external page content that is very long. " + "x " * 100,
                "resolved_action": "fetch_html",
                "fetched_sha256": "b" * 64,
                "final_url": "https://other.example/story",
            }],
        )
        self.assertNotIn("Unrelated external", body.canonical_body_text)
        external = next(item for item in body.sources if item.source.source_kind == "external")
        self.assertEqual(external.disposition, "supporting")

    def test_legacy_flattened_external_cannot_be_promoted(self) -> None:
        row = dict(self.row)
        row["external_text"] = "Issuer Reports Record Quarterly Revenue " + "body " * 100
        body = render_canonical_body({"title": self.row["title"]}, normalized_row=row)
        self.assertEqual(body.body_status, "missing")
        self.assertEqual(body.sources[0].disposition, "excluded")
        self.assertEqual(body.sources[0].reason, "legacy_flattened_source_not_promotable")

    def test_strict_matching_external_can_fill_missing_provider_body(self) -> None:
        payload = {"title": self.row["title"]}
        body = render_canonical_body(
            payload,
            normalized_row=self.row,
            enrichment_rows=[{
                "raw_html": "<nav>Skip to content Sign in Menu</nav><article><h1>Issuer Reports Record Quarterly Revenue</h1><p>Issuer Reports Record Quarterly Revenue after sales increased sharply. " + "Details remained favorable. " * 12 + "</p></article><footer>Privacy Terms Contact</footer>",
                "extracted_text": "Issuer Reports Record Quarterly Revenue after sales increased sharply. " + "Details remained favorable. " * 12,
                "resolved_action": "fetch_html",
                "fetched_sha256": "c" * 64,
                "final_url": "https://issuer.example/release",
            }],
        )
        self.assertEqual(body.primary_source_kind, "external")
        self.assertIn("sales increased sharply", body.canonical_body_text)
        self.assertNotIn("Skip to content", body.canonical_body_text)
        self.assertNotIn("Privacy Terms", body.canonical_body_text)

    def test_rows_preserve_old_hash_and_do_not_mutate_labels(self) -> None:
        payload = {"title": self.row["title"], "body": "<p>Issuer reported record revenue and raised guidance.</p>"}
        body = render_canonical_body(payload, normalized_row=self.row)
        rows = build_body_v3_rows(
            payload,
            self.row,
            body,
            previous_rendered_text_hash="d" * 64,
            previous_renderer_version="old-renderer",
        )
        self.assertEqual(rows["rendered"]["text_contract"], BODY_TEXT_CONTRACT)
        self.assertEqual(rows["lineage"]["previous_rendered_text_hash"], "d" * 64)
        self.assertEqual(rows["lineage"]["label_mutation_status"], "not_mutated")
        self.assertNotIn("operator_label", rows["lineage"])

    def test_purity_detector_rejects_wrappers_binary_and_markers(self) -> None:
        reasons = body_purity_reasons("Title: wrapper\nRead Also:\ndata:image/png;base64,AAAA")
        self.assertIn("source_wrapper", reasons)
        self.assertIn("embedded_binary_or_data_uri", reasons)

    def test_disclosure_and_replacement_character_are_removed(self) -> None:
        payload = {
            "title": self.row["title"],
            "body": """
                <p>The issuer signed the agreement � management expects revenue this quarter.</p>
                <p>Readers are advised that the above article is solely for information purposes and is not investment advice.</p>
            """,
        }
        body = render_canonical_body(payload, normalized_row=self.row)
        self.assertIn("agreement — management", body.canonical_body_text)
        self.assertNotIn("Readers are advised", body.canonical_body_text)
        self.assertFalse(body_purity_reasons(body.canonical_body_text))

    def test_related_article_ctas_without_colons_are_removed(self) -> None:
        payload = {
            "title": self.row["title"],
            "body": """
                <p>The issuer reported record quarterly revenue.</p>
                <p>Read full article &gt; HERE</p>
                <p>Read also Wired article &gt; E-Books have a future</p>
                <p>Management raised full-year guidance.</p>
            """,
        }
        body = render_canonical_body(payload, normalized_row=self.row)
        self.assertNotIn("Read full article", body.canonical_body_text)
        self.assertNotIn("Read also Wired", body.canonical_body_text)
        self.assertIn("Management raised", body.canonical_body_text)

    def test_related_article_cta_punctuation_variants_are_removed(self) -> None:
        for marker in (
            "Read Next, A Different Company Reports Results",
            "Read Next; A Different Company Reports Results",
            "Read Next.",
            'Read Next"',
            "Read Also; A Different Company Reports Results",
        ):
            with self.subTest(marker=marker):
                payload = {
                    "title": self.row["title"],
                    "body": f"<p>The issuer reported revenue.</p><p>{marker}</p><p>Guidance increased.</p>",
                }
                body = render_canonical_body(payload, normalized_row=self.row)
                self.assertNotIn(marker, body.canonical_body_text)
                self.assertIn("Guidance increased.", body.canonical_body_text)

    def test_legitimate_portuguese_a_tilde_is_not_mojibake(self) -> None:
        self.assertFalse(body_purity_reasons("SÃO PAULO — the company reported results."))

    def test_repeated_utf8_mojibake_is_repaired_without_deleting_meaning(self) -> None:
        payload = {
            "title": "Clinical update",
            "url": "https://example.com/update",
            "body": (
                "<p>Hyasynth\u00e2\u00c3\u0082\u0080\u00c3\u0082\u0099s program reported "
                "adverse events in \u00e2\u00c3\u0082\u0089\u00a5 2% of subjects "
                "\u00e2\u00c3\u0082\u0080 a favorable result.</p>"
            ),
        }
        body = render_canonical_body(payload)
        self.assertEqual(
            body.canonical_body_text,
            "Hyasynth’s program reported adverse events in ≥ 2% of subjects — a favorable result.",
        )
        self.assertFalse(body_purity_reasons(body.canonical_body_text))

    def test_json_each_row_parser_preserves_c1_next_line_character(self) -> None:
        rows = parse_json_each_rows('{"text":"left\u0085right"}\n{"text":"next"}\n')
        self.assertEqual(rows, [{"text": "left\u0085right"}, {"text": "next"}])

    def test_truncated_cp1252_closing_quote_is_repaired(self) -> None:
        payload = {
            "title": self.row["title"],
            "body": '<p>The "short-formâ€ merger provisions were not available.</p>',
        }
        body = render_canonical_body(payload, normalized_row=self.row)
        self.assertIn('"short-form” merger', body.canonical_body_text)
        self.assertFalse(body_purity_reasons(body.canonical_body_text))

    def test_ascii_normalized_mojibake_em_dash_is_repaired(self) -> None:
        payload = {
            "title": self.row["title"],
            "body": '<p>The company raised financing â€" including a $20 billion equity offering.</p>',
        }
        body = render_canonical_body(payload, normalized_row=self.row)
        self.assertIn("financing — including", body.canonical_body_text)
        self.assertFalse(body_purity_reasons(body.canonical_body_text))

    def test_shadow_writes_require_a_future_expiration(self) -> None:
        missing = BenzingaPipelineConfig(body_v4_shadow_enabled=True)
        self.assertEqual(body_v4_shadow_state(missing), (False, "body_v4_shadow_disabled:missing_end_utc"))
        future = BenzingaPipelineConfig(
            body_v4_shadow_enabled=True,
            body_v4_shadow_end_utc=(datetime.now(UTC) + timedelta(days=2)).isoformat(),
        )
        self.assertEqual(body_v4_shadow_state(future), (True, ""))
        expired = BenzingaPipelineConfig(
            body_v4_shadow_enabled=True,
            body_v4_shadow_end_utc=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        )
        self.assertEqual(body_v4_shadow_state(expired), (False, "body_v4_shadow_expired"))


if __name__ == "__main__":
    unittest.main()
