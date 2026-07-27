from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock
from urllib import error as url_error

from pipelines.news.benzinga.news_benzinga_rendered_v2_rebuild import (
    DEFAULT_OUTPUT_ROOT,
    RetryingClickHouseHttpClient,
    clickhouse_operation,
    is_transient_clickhouse_error,
)
from research.mlops.clickhouse import ClickHouseHttpClient


class BenzingaRenderedV2RebuildRetryTests(unittest.TestCase):
    def test_transport_timeout_is_retried_and_recorded(self) -> None:
        client = RetryingClickHouseHttpClient(
            "http://clickhouse.invalid:8123",
            "",
            "",
            attempts=3,
            retry_base_seconds=0,
            retry_max_seconds=0,
            status_path=Path("unused-in-mocked-test.jsonl"),
        )
        timeout = url_error.URLError(TimeoutError(10060, "connect timed out"))
        with (
            mock.patch.object(
                ClickHouseHttpClient,
                "execute",
                side_effect=[timeout, "ok"],
            ) as execute,
            mock.patch("pipelines.news.benzinga.news_benzinga_rendered_v2_rebuild.time.sleep"),
            mock.patch(
                "pipelines.news.benzinga.news_benzinga_rendered_v2_rebuild.append_jsonl"
            ) as append_jsonl,
        ):
            result = client.execute(
                "INSERT INTO `q_live`.`benzinga_news_rendered_v2` "
                "(`canonical_news_id`) FORMAT JSONEachRow\n{\"canonical_news_id\":\"abc\"}"
            )
        self.assertEqual(result, "ok")
        self.assertEqual(execute.call_count, 2)
        event = append_jsonl.call_args.args[1]
        self.assertEqual(event["event"], "clickhouse_retry")
        self.assertEqual(
            event["operation"],
            "insert:q_live.benzinga_news_rendered_v2",
        )
        self.assertNotIn("canonical_news_id", str(event))

    def test_non_transient_query_error_is_not_retried(self) -> None:
        client = RetryingClickHouseHttpClient(
            "http://clickhouse.invalid:8123",
            "",
            "",
            attempts=3,
            retry_base_seconds=0,
            retry_max_seconds=0,
            status_path=Path("unused-in-mocked-test.jsonl"),
        )
        with mock.patch.object(
            ClickHouseHttpClient,
            "execute",
            side_effect=RuntimeError("ClickHouse query syntax error"),
        ) as execute:
            with self.assertRaisesRegex(RuntimeError, "syntax error"):
                client.execute("SELECT broken")
        self.assertEqual(execute.call_count, 1)

    def test_nested_windows_connect_timeout_is_transient(self) -> None:
        cause = TimeoutError()
        cause.winerror = 10060  # type: ignore[attr-defined]
        wrapped = RuntimeError("outer")
        wrapped.__cause__ = url_error.URLError(cause)
        self.assertTrue(is_transient_clickhouse_error(wrapped))

    def test_retry_logging_uses_only_bounded_operation_identity(self) -> None:
        sql = (
            "INSERT INTO `q_live`.`benzinga_news_block_v2` (`block_text`) "
            "FORMAT JSONEachRow\n{\"block_text\":\"sensitive article text\"}"
        )
        operation = clickhouse_operation(sql)
        self.assertEqual(operation, "insert:q_live.benzinga_news_block_v2")
        self.assertNotIn("sensitive", operation)

    def test_default_audit_root_is_outside_repository(self) -> None:
        self.assertEqual(
            DEFAULT_OUTPUT_ROOT,
            Path("D:/TradingML/runtimes/news/benzinga_news_rendered_v2"),
        )


if __name__ == "__main__":
    unittest.main()
