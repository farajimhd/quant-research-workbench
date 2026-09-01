from __future__ import annotations

import unittest

from pipelines.news.benzinga.core.clickhouse_writer_body_v4 import (
    body_v4_target_config,
    write_many_news_pipeline_results_body_v4,
)
from pipelines.news.benzinga.core.contracts import NewsPipelineResult, UrlResolution
from pipelines.news.benzinga.news_benzinga_body_v4 import (
    BODY_CLEANER_VERSION,
    BODY_RENDERER_VERSION,
    BODY_TEXT_CONTRACT,
    body_purity_reasons,
    build_body_v4_rows,
    render_canonical_body,
)


class BodyOnlyNewsRendererV4Test(unittest.TestCase):
    def setUp(self) -> None:
        self.row = {
            "provider": "benzinga",
            "provider_article_id": "44159237",
            "canonical_news_id": "4817df7487bdd78c4d5bc5b3844d3d14",
            "published_date": "2025-03-06",
            "published_at_utc": "2025-03-06 07:00:50.000000000",
            "published_raw": "2025-03-06T07:00:50Z",
            "last_updated_at_utc": None,
            "last_updated_raw": "",
            "downloaded_at_utc": "2025-03-06 07:00:51.000000000",
            "provider_delay_ns": 1_000_000_000,
            "title": "Amgen Challenges Novo Nordisk",
            "normalized_title": "amgen challenges novo nordisk",
            "teaser": "",
            "article_url": "https://example.com/news/44159237",
            "url_domain": "example.com",
            "author": "Reporter",
            "tickers": ["AMGN", "NVO"],
            "channels": [],
            "provider_tags": [],
            "image_urls": [],
            "links": [],
            "raw_artifact_path": "raw.json",
            "raw_payload_hash": "a" * 64,
            "content_quality_flags": [],
        }

    def test_bullet_prefixed_read_more_is_excluded(self) -> None:
        payload = {
            "title": self.row["title"],
            "body": (
                "<p>Amgen started two late-stage trials for its monthly injection.</p>"
                "<ul><li>READ MORE: AOC Rips Into Elon Musk</li></ul>"
            ),
        }
        body = render_canonical_body(payload, normalized_row=self.row)
        self.assertEqual(body.canonical_body_text, "Amgen started two late-stage trials for its monthly injection.")
        related = next(block for source in body.sources for block in source.blocks if "READ MORE" in block.original_text)
        self.assertEqual((related.block_role, related.disposition), ("related_content", "excluded"))
        self.assertFalse(body_purity_reasons(body.canonical_body_text))

    def test_structural_bullet_variants_and_ctas_are_excluded(self) -> None:
        variants = (
            "- Read More: Other Company Reports Results",
            "* Read Also: Other Company Reports Results",
            "• See Also: Other Company Reports Results",
            "1. Read Next: Other Company Reports Results",
            "Read more...",
            "Read morehttps://example.com/other",
            "To read more about this story, click here",
            "To read more about this story, click here.",
            "To read more interviews with other women, you can find them on the forum page.",
            "Continue reading at Benzinga Pro",
            "- Read more…",
            "Read Morer At Forexlive",
        )
        for value in variants:
            with self.subTest(value=value):
                payload = {"title": self.row["title"], "body": f"<p>Article body remains.</p><p>{value}</p>"}
                body = render_canonical_body(payload, normalized_row=self.row)
                self.assertEqual(body.canonical_body_text, "Article body remains.")

    def test_middle_marker_removes_only_related_link_and_resumes_body(self) -> None:
        payload = {
            "title": self.row["title"],
            "body": (
                "<p>First article paragraph.</p><p>- Read Also:</p>"
                "<p>Other Company Reports Surprise Results</p><p>Second article paragraph.</p>"
            ),
        }
        body = render_canonical_body(payload, normalized_row=self.row)
        self.assertEqual(body.canonical_body_text, "First article paragraph.\nSecond article paragraph.")

    def test_terminal_inline_marker_is_trimmed_without_losing_article_text(self) -> None:
        payload = {
            "title": self.row["title"],
            "body": "<p>The trial will evaluate weight loss over 72 weeks. - READ MORE: Unrelated Political Story</p>",
        }
        body = render_canonical_body(payload, normalized_row=self.row)
        self.assertEqual(body.canonical_body_text, "The trial will evaluate weight loss over 72 weeks.")

    def test_multiline_social_embed_cta_is_removed_without_losing_quote_or_following_body(self) -> None:
        payload = {
            "title": self.row["title"],
            "body": (
                "<p>Quoted post announced a tournament.\nRead More👇https://example.com/promo\n"
                "The quoted post listed a giveaway.</p><p>The article analysis resumes here.</p>"
            ),
        }
        body = render_canonical_body(payload, normalized_row=self.row)
        self.assertNotIn("Read More", body.canonical_body_text)
        self.assertIn("Quoted post announced", body.canonical_body_text)
        self.assertIn("listed a giveaway", body.canonical_body_text)
        self.assertIn("analysis resumes", body.canonical_body_text)

    def test_legitimate_lowercase_phrase_is_preserved(self) -> None:
        payload = {
            "title": self.row["title"],
            "body": "<p>Investors can read more detail in the clinical protocol filed with regulators.</p>",
        }
        body = render_canonical_body(payload, normalized_row=self.row)
        self.assertIn("read more detail", body.canonical_body_text)

    def test_rows_carry_v4_contract_and_label_non_mutation(self) -> None:
        payload = {"title": self.row["title"], "body": "<p>Article body remains.</p>"}
        body = render_canonical_body(payload, normalized_row=self.row)
        rows = build_body_v4_rows(
            payload,
            self.row,
            body,
            previous_rendered_text_hash="b" * 64,
            previous_renderer_version="benzinga_body_renderer_v3",
        )
        self.assertEqual(rows["rendered"]["cleaner_version"], BODY_CLEANER_VERSION)
        self.assertEqual(rows["rendered"]["renderer_version"], BODY_RENDERER_VERSION)
        self.assertEqual(rows["rendered"]["text_contract"], BODY_TEXT_CONTRACT)
        self.assertEqual(rows["lineage"]["label_mutation_status"], "not_mutated")

    def test_purity_guard_independently_detects_bullet_and_inline_markers(self) -> None:
        self.assertIn("related_content_marker", body_purity_reasons("Body\n- READ MORE: Other story"))
        self.assertIn("inline_related_content", body_purity_reasons("Body sentence. - READ MORE: Other story"))

    def test_v4_writer_targets_only_the_v4_table_family(self) -> None:
        target = body_v4_target_config(require_certified=True)
        self.assertEqual(target.event_table, "benzinga_news_event_v4")
        self.assertEqual(target.rendered_table, "benzinga_news_rendered_v4")
        self.assertEqual(target.lineage_table, "benzinga_news_body_lineage_v2")
        self.assertEqual(target.renderer_version, BODY_RENDERER_VERSION)
        self.assertTrue(target.require_certified)

    def test_live_writer_rejects_contaminated_body_before_insert(self) -> None:
        result = NewsPipelineResult(
            provider_article_id="44159237",
            canonical_news_id=self.row["canonical_news_id"],
            policy_version="test",
            normalized_row={},
            ticker_links=[],
            url_resolution=UrlResolution("test", [], [], [], {}),
            body_event_row={"published_date": self.row["published_date"]},
            body_rendered_row={
                "renderer_version": BODY_RENDERER_VERSION,
                "canonical_body_text": "Article body.\n- READ MORE: Unrelated story",
            },
        )
        with self.assertRaisesRegex(RuntimeError, "Body V4 purity rejected"):
            write_many_news_pipeline_results_body_v4(
                None,  # type: ignore[arg-type]
                [result],
                config=body_v4_target_config(skip_table_validation=True),
            )


if __name__ == "__main__":
    unittest.main()
