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
    v2_batch_query_id,
)
from research.mlops.clickhouse import ClickHouseHttpClient, ClickHouseHttpStatusError


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

    def test_persistent_http_status_preserves_retry_semantics(self) -> None:
        self.assertTrue(
            is_transient_clickhouse_error(
                ClickHouseHttpStatusError(503, "Unavailable", "retry")
            )
        )
        self.assertFalse(
            is_transient_clickhouse_error(
                ClickHouseHttpStatusError(400, "Bad Request", "contract error")
            )
        )

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

    def test_persistent_client_reuses_connection_and_applies_sync_settings(self) -> None:
        connection = mock.MagicMock()
        first = mock.MagicMock(status=200, reason="OK", will_close=False)
        first.read.return_value = b"one"
        second = mock.MagicMock(status=200, reason="OK", will_close=False)
        second.read.return_value = b"two"
        connection.getresponse.side_effect = [first, second]
        with mock.patch(
            "research.mlops.clickhouse.http.client.HTTPConnection",
            return_value=connection,
        ) as connection_type:
            client = ClickHouseHttpClient(
                "http://clickhouse.invalid:8123",
                "user",
                "password",
                timeout_seconds=17,
                persistent=True,
                default_query_params={"async_insert": 0, "wait_end_of_query": 1},
            )
            self.assertEqual(client.execute("SELECT 1", query_id="stable-id"), "one")
            self.assertEqual(client.execute("SELECT 2"), "two")
            client.close()
        self.assertEqual(connection_type.call_count, 1)
        self.assertEqual(connection.request.call_count, 2)
        first_path = connection.request.call_args_list[0].args[1]
        self.assertIn("async_insert=0", first_path)
        self.assertIn("wait_end_of_query=1", first_path)
        self.assertIn("query_id=stable-id", first_path)

    def test_persistent_client_reconnects_after_remote_disconnect(self) -> None:
        disconnected_connection = mock.MagicMock()
        disconnected_connection.getresponse.side_effect = RemoteDisconnected("closed")
        healthy_connection = mock.MagicMock()
        response = mock.MagicMock(status=200, reason="OK", will_close=False)
        response.read.return_value = b"ok"
        healthy_connection.getresponse.return_value = response
        with mock.patch(
            "research.mlops.clickhouse.http.client.HTTPConnection",
            side_effect=[disconnected_connection, healthy_connection],
        ) as connection_type:
            client = ClickHouseHttpClient(
                "http://clickhouse.invalid:8123",
                "",
                "",
                persistent=True,
            )
            with self.assertRaises(RemoteDisconnected):
                client.execute("SELECT 1")
            self.assertEqual(client.execute("SELECT 1"), "ok")
        self.assertEqual(connection_type.call_count, 2)
        disconnected_connection.close.assert_called_once()

    def test_lost_insert_response_is_reconciled_before_retry(self) -> None:
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
        with (
            mock.patch.object(
                ClickHouseHttpClient,
                "execute",
                side_effect=RemoteDisconnected("closed"),
            ) as execute,
            mock.patch.object(client, "_reconcile_insert", return_value=True) as reconcile,
            mock.patch(
                "pipelines.news.benzinga.news_benzinga_rendered_v2_rebuild.append_jsonl"
            ) as append_jsonl,
        ):
            result = client.execute(
                "INSERT INTO t FORMAT JSONEachRow\n{}",
                query_id="stable-batch-id",
            )
        self.assertEqual(result, "")
        self.assertEqual(execute.call_count, 1)
        reconcile.assert_called_once_with("stable-batch-id")
        self.assertEqual(
            append_jsonl.call_args.args[1]["event"],
            "clickhouse_insert_reconciled",
        )

    def test_reconciliation_accepts_finished_query_log_record(self) -> None:
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
        with mock.patch.object(
            ClickHouseHttpClient,
            "execute",
            side_effect=[
                "0",
                "",
                '{"event_type":"QueryFinish","exception_code":0,"exception":""}\n',
            ],
        ):
            self.assertTrue(client._reconcile_insert("stable-batch-id"))

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

    def test_renderer_batch_identity_covers_full_rendered_text(self) -> None:
        rows = [{"canonical_news_id": "abc", "rendered_text": "complete text"}]
        first = v2_batch_query_id(
            "benzinga_news_rendered_v2",
            1,
            rows,
        )
        repeated = v2_batch_query_id(
            "benzinga_news_rendered_v2",
            1,
            list(rows),
        )
        changed = v2_batch_query_id(
            "benzinga_news_rendered_v2",
            1,
            [{"canonical_news_id": "abc", "rendered_text": "different text"}],
        )
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, changed)

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
