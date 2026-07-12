from __future__ import annotations

import sys
import unittest
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest import mock

from pipelines.market_sip.events import clickhouse_build_sec_context as sec_context
from pipelines.market_sip.events import clickhouse_build_text_tokens as text_tokens
from pipelines.market_sip.events import run_build_qwen_text_embeddings as qwen_launcher
from services.text_embed_gateway.gateway import (
    missing_sec_source_sql,
    sec_available_coverage_sql,
    sec_source_gap_summary_sql,
)


def historical_args() -> SimpleNamespace:
    return SimpleNamespace(
        source_database="q_live",
        target_database="market_sip_compact",
        sec_filing_table="sec_filing_v3",
        sec_document_table="sec_filing_document_v3",
        sec_rendered_text_table="sec_filing_text_rendered_v3",
        sec_bridge_table="id_sec_market_bridge_v3",
        limit_rows_per_chunk=0,
        max_threads=4,
        max_memory_usage="4G",
    )


def gateway_config() -> SimpleNamespace:
    return SimpleNamespace(
        source_database="q_live",
        target_database="market_sip_compact",
        sec_live_filing_table="sec_filing_v3",
        sec_live_document_table="sec_filing_document_v3",
        sec_live_rendered_text_table="sec_filing_text_rendered_v3",
        sec_bridge_table="id_sec_market_bridge_v3",
        sec_token_table="sec_filing_text_tokens_v3",
        sec_embedding_table="sec_filing_text_embeddings_v3",
        tokenizer_model="Qwen/Qwen3-0.6B",
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        embedding_pooling="last_token",
        source_batch_size=64,
        historical_batch_limit=512,
        max_threads=4,
        max_memory_usage="4G",
    )


class SecRenderedEmbeddingSourceTests(unittest.TestCase):
    def test_historical_source_reads_rendered_document_rows_directly(self) -> None:
        sql = text_tokens.sec_source_sql(
            historical_args(),
            chunk_start=date(2026, 7, 1),
            chunk_end=date(2026, 7, 2),
        )

        self.assertIn("`q_live`.`sec_filing_text_rendered_v3`", sql)
        self.assertIn("`q_live`.`sec_filing_document_v3`", sql)
        self.assertIn("`q_live`.`sec_filing_v3`", sql)
        self.assertIn("`q_live`.`id_sec_market_bridge_v3`", sql)
        self.assertIn("valid_from_date <= toDate(f.accepted_at_utc)", sql)
        self.assertIn("valid_to_date_exclusive > toDate(f.accepted_at_utc)", sql)
        self.assertIn("concat(accession_number, ':', toString(text_rank), ':', document_id)", sql)
        self.assertNotIn("sec_filing_text_context_v3", sql)

    def test_live_source_gap_and_coverage_queries_use_same_direct_cte(self) -> None:
        bounds = (
            datetime(2026, 7, 1, tzinfo=UTC),
            datetime(2026, 7, 2, tzinfo=UTC),
        )
        config = gateway_config()

        statements = [
            missing_sec_source_sql(config, bounds, "historical"),
            sec_source_gap_summary_sql(config, bounds),
            sec_available_coverage_sql(config, bounds),
        ]

        for sql in statements:
            self.assertIn("sec_rendered_source AS", sql)
            self.assertIn("`q_live`.`sec_filing_text_rendered_v3`", sql)
            self.assertNotIn("sec_filing_text_context_v3", sql)

    def test_qwen_launcher_defaults_to_combined_source_text_mode(self) -> None:
        captured: dict[str, list[str]] = {}

        def fake_main() -> int:
            captured["argv"] = list(sys.argv)
            return 0

        with mock.patch.object(qwen_launcher._text_token_launcher, "main", fake_main), mock.patch.object(
            sys, "argv", ["run_build_qwen_text_embeddings.py"]
        ):
            result = qwen_launcher.main()

        self.assertEqual(result, 0)
        self.assertIn("source_text", captured["argv"])
        self.assertIn("--build-embeddings", captured["argv"])

    def test_sec_context_builder_skips_legacy_text_copy_by_default(self) -> None:
        with mock.patch.object(
            sys,
            "argv",
            ["clickhouse_build_sec_context.py"],
        ):
            args = sec_context.parse_args()

        self.assertTrue(args.skip_text)


if __name__ == "__main__":
    unittest.main()
