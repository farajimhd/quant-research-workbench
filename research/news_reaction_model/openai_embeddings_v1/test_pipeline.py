from __future__ import annotations

import base64
import struct
import unittest
from decimal import Decimal

from research.mlops.clickhouse import insert_json_each_row
from research.news_reaction_model.openai_embeddings_v1.openai_api import OpenAIAPIError
from research.news_reaction_model.openai_embeddings_v1.pipeline import decode_embedding, money_for_tokens, month_ranges
from research.news_reaction_model.openai_embeddings_v1.run_build import parse_args


class OpenAIEmbeddingPipelineTests(unittest.TestCase):
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
