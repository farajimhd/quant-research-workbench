from __future__ import annotations

import base64
import struct
import unittest
from decimal import Decimal

from research.mlops.clickhouse import insert_json_each_row
from research.news_reaction_model.openai_embeddings_v1.config import PipelineConfig
from research.news_reaction_model.openai_embeddings_v1.openai_api import OpenAIAPIError
from research.news_reaction_model.openai_embeddings_v1.pipeline import (
    decode_embedding,
    existing_month_items,
    money_for_tokens,
    month_ranges,
    prepare_text,
)
from research.news_reaction_model.openai_embeddings_v1.run_build import parse_args


class OpenAIEmbeddingPipelineTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
