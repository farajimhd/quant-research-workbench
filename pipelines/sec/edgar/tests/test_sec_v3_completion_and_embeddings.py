from __future__ import annotations

import unittest
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pipelines.market_sip.events import clickhouse_build_text_tokens as tokens
from pipelines.sec.edgar import sec_acceptance_raw_metadata_repair as acceptance_repair
from pipelines.sec.edgar import sec_historical_gap_fill as historical


class SecV3CompletionAndEmbeddingTests(unittest.TestCase):
    def test_required_archive_date_uses_last_completed_weekday(self) -> None:
        self.assertEqual(
            historical.required_archive_through_date("2026-07-12", today_utc=date(2026, 7, 13)),
            date(2026, 7, 10),
        )
        self.assertEqual(
            historical.required_archive_through_date("2026-07-15", today_utc=date(2026, 7, 14)),
            date(2026, 7, 13),
        )

    def test_incremental_gap_fill_limits_archive_work_but_reconciles_full_context(self) -> None:
        with mock.patch.object(
            sys,
            "argv",
            ["sec_historical_gap_fill.py", "--start-date", "2026-07-10", "--end-date", "2026-07-11"],
        ):
            args = historical.parse_args()
        commands = {command.stage: command.command for command in historical.build_commands(args, Path("logs"))}

        self.assertIn("2026-07-10", commands["archive-text-rebuild"])
        context_start = commands["sec-context-build"].index("--start-date") + 1
        self.assertEqual(commands["sec-context-build"][context_start], "2019-01-01")
        self.assertIn("acceptance-raw-metadata-repair", commands)

    def test_raw_acceptance_repair_uses_only_explicit_utc_source_values(self) -> None:
        cte = acceptance_repair.resolved_raw_cte_sql(
            [
                ("sec_core", "sec_bulk_mirror_filing_v3", 20, "sec_core_raw_z_acceptance_repair"),
                ("q_live", "sec_filing_v2", 10, "legacy_raw_z_acceptance_repair"),
            ]
        )
        sql = acceptance_repair.insert_replacements_sql(
            SimpleNamespace(target_database="q_live", target_table="sec_filing_v3"),
            cte,
            "repair-test",
        )

        self.assertIn("endsWith(acceptance_datetime_raw, 'Z')", cte)
        self.assertIn("parseDateTime64BestEffortOrNull", cte)
        self.assertIn("argMax(tuple(raw_value, repaired_source), source_priority)", cte)
        self.assertIn("r.accepted_at_utc AS accepted_at_utc", sql)
        self.assertIn("r.acceptance_datetime_raw AS acceptance_datetime_raw", sql)
        self.assertIn("f.accepted_at_source IN", sql)

    def test_sec_chunks_are_complete_when_max_chunks_is_zero(self) -> None:
        chunks = tokens.make_sec_chunks(list(range(10_001)), chunk_tokens=1024, max_chunks=0)

        self.assertEqual(len(chunks), 10)
        self.assertEqual(chunks[-1].token_end, 10_001)
        self.assertTrue(all(chunk.was_truncated == 0 for chunk in chunks))

    def test_positive_max_chunks_still_caps_news(self) -> None:
        chunks = tokens.make_news_chunks(list(range(10_001)), chunk_tokens=1024, max_chunks=2)

        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(chunk.was_truncated == 1 for chunk in chunks))

    def test_sec_v3_schema_supports_long_documents_and_time_provenance(self) -> None:
        token_sql = tokens.create_sec_token_table_sql("market_sip_compact", "sec_filing_text_tokens_v3", "")
        embedding_sql = tokens.create_sec_embedding_table_sql("market_sip_compact", "sec_filing_text_embeddings_v3", "")

        for sql in (token_sql, embedding_sql):
            self.assertIn("token_chunk_index UInt16", sql)
            self.assertIn("accepted_at_source LowCardinality(String)", sql)
            self.assertIn("event_time_quality LowCardinality(String)", sql)

    def test_embedding_source_excludes_date_only_fallback_times(self) -> None:
        sql = tokens.sec_rendered_source_ctes_sql(
            source_database="q_live",
            filing_table="sec_filing_v3",
            document_table="sec_filing_document_v3",
            rendered_text_table="sec_filing_text_rendered_v3",
            bridge_table="id_sec_market_bridge_v3",
            start_sql="toDateTime64('2026-07-01', 9, 'UTC')",
            end_sql="toDateTime64('2026-07-02', 9, 'UTC')",
        )

        self.assertIn("f.accepted_at_source NOT IN", sql)
        self.assertIn("archive_filing_date_midnight", sql)
        self.assertIn("'exact' AS event_time_quality", sql)


if __name__ == "__main__":
    unittest.main()
