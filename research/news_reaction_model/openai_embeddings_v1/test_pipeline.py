from __future__ import annotations

import base64
import struct
import unittest
from decimal import Decimal
from urllib import error as url_error

from research.mlops.clickhouse import insert_json_each_row
from research.news_reaction_model.openai_embeddings_v1.config import PipelineConfig
from research.news_reaction_model.openai_embeddings_v1.openai_api import OpenAIAPIError
from research.news_reaction_model.openai_embeddings_v1.pipeline import (
    decode_embedding,
    clickhouse_utc_now,
    execute_read,
    existing_month_items,
    is_transient_clickhouse_transport_error,
    money_for_tokens,
    month_ranges,
    planned_source_rows,
    prepare_text,
)
from research.news_reaction_model.openai_embeddings_v1.run_build import parse_args


class OpenAIEmbeddingPipelineTests(unittest.TestCase):
    def test_planned_source_projection_has_stable_unqualified_json_keys(self) -> None:
        class Client:
            sql = ""

            def execute(self, sql: str) -> str:
                self.sql = sql
                return ""

        client = Client()
        self.assertEqual(planned_source_rows(client, PipelineConfig(), limit=1), [])
        self.assertIn("i.canonical_news_id AS canonical_news_id", client.sql)
        self.assertIn("i.ticker AS ticker", client.sql)
        self.assertIn("i.text_sha256 AS text_sha256", client.sql)

    def test_clickhouse_timestamp_uses_native_datetime64_text(self) -> None:
        value = clickhouse_utc_now()
        self.assertRegex(value, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$")
        self.assertNotIn("T", value)
        self.assertFalse(value.endswith("Z"))

    def test_reserved_token_spelling_is_encoded_as_article_content(self) -> None:
        import tiktoken

        prepared = prepare_text(
            {
                "ticker": "NVDA",
                "published_at_utc": "2024-06-14 16:08:24.000000000",
                "external_text": "Literal source text <|endoftext|> remains content.",
            },
            PipelineConfig(),
            tiktoken.encoding_for_model("text-embedding-3-large"),
        )
        self.assertGreater(prepared.input_tokens, 0)
        self.assertIn("<|endoftext|>", prepared.text)

    def test_existing_item_date_filter_is_not_replaced_by_string_alias(self) -> None:
        import datetime as dt

        class Client:
            sql = ""

            def execute(self, sql: str) -> str:
                self.sql = sql
                return ""

        client = Client()
        result = existing_month_items(
            client,
            PipelineConfig(),
            dt.date(2024, 6, 1),
            dt.date(2024, 7, 1),
        )
        self.assertEqual(result, {})
        self.assertIn("FROM\n(\n SELECT", client.sql)
        self.assertIn("AND published_at_utc >= toDateTime64", client.sql)

    def test_shared_json_each_row_insert_quotes_identifiers(self) -> None:
        class Client:
            sql = ""

            def execute(self, sql: str) -> str:
                self.sql = sql
                return ""

        client = Client()
        insert_json_each_row(client, "db-name", "table", ["id", "value"], [{"id": 1, "value": "x"}])
        self.assertIn("INSERT INTO `db-name`.`table` (`id`, `value`)", client.sql)
        self.assertIn('{"id":1,"value":"x"}', client.sql)

    def test_money_uses_exact_decimal_arithmetic(self) -> None:
        self.assertEqual(money_for_tokens(2_500_000, Decimal("0.26")), Decimal("0.650000"))

    def test_month_ranges_cover_end_exclusive(self) -> None:
        ranges = month_ranges("2026-01-15", "2026-03-10")
        self.assertEqual(ranges[0][0].isoformat(), "2026-01-01")
        self.assertEqual(ranges[-1][1].isoformat(), "2026-03-10")

    def test_base64_float32_decode(self) -> None:
        payload = base64.b64encode(struct.pack("<3f", 1.25, -2.5, 0.0)).decode()
        self.assertEqual(decode_embedding(payload, 3), [1.25, -2.5, 0.0])

    def test_budget_cannot_be_raised_above_compiled_limit(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(["--max-cost-usd", "50.01"])

    def test_api_error_is_a_real_runtime_error(self) -> None:
        error = OpenAIAPIError(429, "insufficient_quota", "billing", "No credit", False)
        self.assertIsInstance(error, RuntimeError)
        self.assertTrue(error.is_quota_error)
        self.assertIn("insufficient_quota", str(error))

    def test_clickhouse_read_retries_transient_connect_timeout(self) -> None:
        class Client:
            calls = 0

            def execute(self, sql: str) -> str:
                self.calls += 1
                if self.calls < 3:
                    raise url_error.URLError(TimeoutError(10060, "connect timed out"))
                return "7"

        client = Client()
        config = PipelineConfig(
            clickhouse_read_attempts=3,
            clickhouse_retry_delay_seconds=0.0,
            clickhouse_retry_max_delay_seconds=0.0,
        )
        self.assertEqual(execute_read(client, config, "SELECT 7", operation="test"), "7")
        self.assertEqual(client.calls, 3)

    def test_clickhouse_read_does_not_retry_query_errors(self) -> None:
        class Client:
            calls = 0

            def execute(self, sql: str) -> str:
                self.calls += 1
                raise RuntimeError("ClickHouse query contract is invalid")

        client = Client()
        with self.assertRaisesRegex(RuntimeError, "query contract"):
            execute_read(client, PipelineConfig(), "SELECT broken", operation="test")
        self.assertEqual(client.calls, 1)

    def test_clickhouse_transport_classifier_follows_nested_url_reason(self) -> None:
        self.assertTrue(
            is_transient_clickhouse_transport_error(
                url_error.URLError(TimeoutError(10060, "connect timed out"))
            )
        )
        self.assertFalse(is_transient_clickhouse_transport_error(RuntimeError("query failed")))


if __name__ == "__main__":
    unittest.main()
