from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from src.backend.historical_signal_occurrence_service import (
    historical_source_native_signal_occurrences,
)


class _Client:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def execute(self, sql: str) -> str:
        self.queries.append(sql)
        if "count() AS row_count" in sql:
            return json.dumps({"row_count": len(self.rows)}) + "\n"
        return "\n".join(json.dumps(row) for row in self.rows) + "\n"


class HistoricalSignalOccurrenceServiceTests(unittest.TestCase):
    def test_loads_hashed_occurrences_at_original_availability_clock(self) -> None:
        payload = {
            "event_id": "event-1",
            "signal_stream_id": "price-squeeze-5m",
            "ticker": "AAPL",
            "event_time": "2026-08-21T12:00:00Z",
            "available_at": "2026-08-21T12:00:00.100000Z",
            "squeeze_move_pct": 5.1,
        }
        client = _Client([{
            "event_id": "event-1",
            "sequence": 7,
            "event_time": "2026-08-21 12:00:00.000000",
            "configuration_revision": "configuration-1",
            "definition_revision": "definition-1",
            "payload_json": json.dumps(payload),
        }])

        with patch.dict(
            "os.environ",
            {
                "QMD_CLICKHOUSE_DATABASE": "q_live",
                "QMD_SIGNAL_STREAM_TABLE": "signal_stream_occurrence_v1",
            },
        ):
            result = historical_source_native_signal_occurrences(
                {
                    "signal_stream_id": "price-squeeze-5m",
                    "occurrence_source": "qmd_squeeze_episode",
                },
                start=datetime(2026, 8, 21, 8, tzinfo=UTC),
                end=datetime(2026, 8, 22, tzinfo=UTC),
                client=client,
            )

        self.assertEqual(result["occurrences"][0]["ticker"], "AAPL")
        self.assertEqual(result["authority"]["row_count"], 1)
        self.assertEqual(result["authority"]["definition_revisions"], ["definition-1"])
        self.assertEqual(len(result["authority"]["content_hash"]), 64)
        self.assertIn("FINAL", client.queries[1])
        self.assertIn("ORDER BY event_time,sequence,event_id", client.queries[1])

    def test_rejects_occurrence_available_before_event_time(self) -> None:
        client = _Client([{
            "event_id": "event-1",
            "sequence": 1,
            "event_time": "2026-08-21 12:00:00.000000",
            "configuration_revision": "configuration-1",
            "definition_revision": "definition-1",
            "payload_json": json.dumps({
                "event_id": "event-1",
                "ticker": "AAPL",
                "available_at": "2026-08-21T11:59:59Z",
            }),
        }])

        with self.assertRaisesRegex(RuntimeError, "available before its event clock"):
            historical_source_native_signal_occurrences(
                {
                    "signal_stream_id": "price-squeeze-5m",
                    "occurrence_source": "qmd_squeeze_episode",
                },
                start=datetime(2026, 8, 21, 8, tzinfo=UTC),
                end=datetime(2026, 8, 22, tzinfo=UTC),
                client=client,
            )

    def test_rejects_unknown_native_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported"):
            historical_source_native_signal_occurrences(
                {
                    "signal_stream_id": "unknown",
                    "occurrence_source": "unversioned_service",
                },
                start=datetime(2026, 8, 21, 8, tzinfo=UTC),
                end=datetime(2026, 8, 22, tzinfo=UTC),
                client=_Client([]),
            )


if __name__ == "__main__":
    unittest.main()
