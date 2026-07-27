from __future__ import annotations

import unittest
from http.client import RemoteDisconnected
from pathlib import Path
from unittest import mock
from urllib import error as url_error

from pipelines.news.benzinga.news_benzinga_rendered_v2_rebuild import (
    DEFAULT_OUTPUT_ROOT,
    RetryingClickHouseHttpClient,
    clickhouse_operation,
    is_transient_clickhouse_error,
    resolve_path,
)
from pipelines.news.benzinga.core.clickhouse_writer_v2 import (
    OversizedNewsRowError,
    insert_v2_json_each_row_bounded,
    json_each_row_batches,
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
            request_timeout_seconds=5,
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
            request_timeout_seconds=5,
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

    def test_remote_disconnected_is_transient(self) -> None:
        self.assertTrue(is_transient_clickhouse_error(RemoteDisconnected("closed")))

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

    def test_clickhouse_client_applies_finite_request_timeout(self) -> None:
        client = ClickHouseHttpClient(
            "http://clickhouse.invalid:8123",
            "",
            "",
            timeout_seconds=17,
        )
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"ok"
        with mock.patch(
            "research.mlops.clickhouse.request.urlopen",
            return_value=response,
        ) as urlopen:
            self.assertEqual(client.execute("SELECT 1"), "ok")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 17)

    def test_json_each_row_batches_respect_count_and_encoded_bytes(self) -> None:
        rows = [
            {"canonical_news_id": f"id-{index}", "block_text": "é" * 12}
            for index in range(5)
        ]
        batches = list(
            json_each_row_batches(
                rows,
                table="benzinga_news_block_v2",
                max_rows=3,
                target_bytes=180,
                max_row_bytes=1_000,
            )
        )
        self.assertEqual([len(batch.rows) for batch in batches], [2, 2, 1])
        self.assertTrue(all(batch.body_bytes <= 180 for batch in batches))
        self.assertEqual(
            [row["canonical_news_id"] for batch in batches for row in batch.rows],
            [row["canonical_news_id"] for row in rows],
        )

    def test_single_row_can_exceed_soft_target_but_not_hard_limit(self) -> None:
        row = {"canonical_news_id": "safe-id", "rendered_text": "x" * 200}
        batches = list(
            json_each_row_batches(
                [row],
                table="benzinga_news_rendered_v2",
                max_rows=500,
                target_bytes=64,
                max_row_bytes=512,
            )
        )
        self.assertEqual(len(batches), 1)
        self.assertGreater(batches[0].body_bytes, 64)

    def test_shared_v2_writer_uses_byte_bounded_batches(self) -> None:
        rows = [
            {"canonical_news_id": f"id-{index}", "block_text": "x" * 50}
            for index in range(5)
        ]
        with mock.patch(
            "pipelines.news.benzinga.core.clickhouse_writer_v2.insert_json_each_row"
        ) as insert:
            insert_v2_json_each_row_bounded(
                mock.sentinel.client,
                "q_live",
                "benzinga_news_block_v2",
                ["canonical_news_id", "block_text"],
                rows,
                max_rows=2,
                target_bytes=10_000,
                max_row_bytes=1_000,
            )
        self.assertEqual(insert.call_count, 3)
        self.assertEqual(
            [len(call.args[4]) for call in insert.call_args_list],
            [2, 2, 1],
        )

    def test_oversized_row_fails_with_identity_without_content(self) -> None:
        row = {
            "canonical_news_id": "safe-id",
            "block_ordinal": 7,
            "block_text": "sensitive article body" * 20,
        }
        with self.assertRaises(OversizedNewsRowError) as raised:
            list(
                json_each_row_batches(
                    [row],
                    table="benzinga_news_block_v2",
                    max_rows=500,
                    target_bytes=128,
                    max_row_bytes=128,
                )
            )
        message = str(raised.exception)
        self.assertIn("canonical_news_id=safe-id", message)
        self.assertIn("block_ordinal=7", message)
        self.assertNotIn("sensitive article body", message)

    def test_retry_event_includes_safe_insert_batch_diagnostics(self) -> None:
        client = RetryingClickHouseHttpClient(
            "http://clickhouse.invalid:8123",
            "",
            "",
            attempts=2,
            retry_base_seconds=0,
            retry_max_seconds=0,
            request_timeout_seconds=5,
            status_path=Path("unused-in-mocked-test.jsonl"),
        )
        disconnected = ConnectionResetError("remote closed")
        with (
            mock.patch.object(
                ClickHouseHttpClient,
                "execute",
                side_effect=[disconnected, "ok"],
            ),
            mock.patch("pipelines.news.benzinga.news_benzinga_rendered_v2_rebuild.time.sleep"),
            mock.patch(
                "pipelines.news.benzinga.news_benzinga_rendered_v2_rebuild.append_jsonl"
            ) as append_jsonl,
        ):
            with client.diagnostic_context(
                day="2024-06-20",
                table="benzinga_news_block_v2",
                batch=2,
                batch_count=3,
                rows=411,
                body_bytes=4_000_000,
                max_row_bytes=24_675,
                forbidden="sensitive article body",
            ):
                self.assertEqual(client.execute("INSERT INTO t FORMAT JSONEachRow\n{}"), "ok")
        event = append_jsonl.call_args.args[1]
        self.assertEqual(event["day"], "2024-06-20")
        self.assertEqual(event["body_bytes"], 4_000_000)
        self.assertNotIn("forbidden", event)
        self.assertNotIn("sensitive", str(event))

    def test_artifact_resolution_prefers_accessible_local_path(self) -> None:
        local = Path("D:/market-data/news-benzinga/raw/article.json")
        with mock.patch(
            "pipelines.news.benzinga.news_benzinga_rendered_v2_rebuild.path_is_accessible",
            side_effect=lambda path: path == local,
        ) as accessible:
            resolved = resolve_path(
                str(local),
                [(r"D:\market-data", r"\\workstation\market-data")],
            )
        self.assertEqual(resolved, local)
        self.assertEqual(accessible.call_count, 1)

    def test_artifact_resolution_uses_mapping_after_local_permission_failure(self) -> None:
        local = Path("D:/market-data/news-benzinga/raw/article.json")
        mapped = Path("//workstation/market-data/news-benzinga/raw/article.json")
        with mock.patch(
            "pipelines.news.benzinga.news_benzinga_rendered_v2_rebuild.path_is_accessible",
            side_effect=lambda path: path == mapped,
        ):
            resolved = resolve_path(
                str(local),
                [(r"D:\market-data", r"\\workstation\market-data")],
            )
        self.assertEqual(resolved, mapped)


if __name__ == "__main__":
    unittest.main()
