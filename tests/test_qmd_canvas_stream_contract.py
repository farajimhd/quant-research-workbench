import unittest
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.backend.app import (
    app,
    _qmd_stream_payload_matches_ticker,
    trading_canvas_market_events,
)


class FakeUpstream:
    def __init__(self, messages: list[dict]) -> None:
        self.messages = [json.dumps(message) for message in messages]

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


class FakeConnect:
    def __init__(self, upstream: FakeUpstream, entered: list[bool]) -> None:
        self.upstream = upstream
        self.entered = entered

    async def __aenter__(self):
        self.entered.append(True)
        return self.upstream

    async def __aexit__(self, *_args):
        return False


class QmdCanvasStreamContractTests(unittest.TestCase):
    def test_route_subscribes_before_snapshot_and_forwards_gap_control_frame(self) -> None:
        entered: list[bool] = []

        def snapshot(*_args, **_kwargs):
            self.assertTrue(entered, "upstream subscription must precede snapshot capture")
            return {
                "buffer_end_arrival_sequence": 19,
                "events": [{"arrival_sequence": 19, "ticker": "AAPL"}],
                "next_after_arrival_sequence": 19,
                "truncated_before": False,
            }

        upstream = FakeUpstream(
            [{"type": "stream_gap", "action": "resnapshot_required", "skipped": 3}]
        )
        with (
            patch(
                "src.backend.app.websockets.connect",
                return_value=FakeConnect(upstream, entered),
            ),
            patch("src.backend.app.qmd_compact_event_page", side_effect=snapshot),
            TestClient(app) as client,
        ):
            with client.websocket_connect(
                "/api/trading/canvas-market-events/stream/AAPL"
            ) as websocket:
                first = websocket.receive_json()
                second = websocket.receive_json()

        self.assertEqual(first["type"], "snapshot")
        self.assertEqual(first["last_sequence"], 19)
        self.assertEqual(second["action"], "resnapshot_required")
        self.assertEqual(second["skipped"], 3)

    def test_stream_control_frames_bypass_ticker_filter(self) -> None:
        self.assertTrue(
            _qmd_stream_payload_matches_ticker(
                {
                    "type": "stream_gap",
                    "action": "resnapshot_required",
                    "skipped": 3,
                },
                "AAPL",
            )
        )
        self.assertTrue(
            _qmd_stream_payload_matches_ticker(
                {"error": "upstream unavailable"},
                "AAPL",
            )
        )

    def test_stream_data_frames_remain_ticker_scoped(self) -> None:
        self.assertTrue(_qmd_stream_payload_matches_ticker({"ticker": "aapl"}, "AAPL"))
        self.assertFalse(_qmd_stream_payload_matches_ticker({"ticker": "MSFT"}, "AAPL"))
        self.assertFalse(_qmd_stream_payload_matches_ticker({"status": "connected"}, "AAPL"))

    def test_market_event_snapshot_exposes_sequence_and_completeness(self) -> None:
        with (
            patch(
                "src.backend.app.qmd_compact_event_page",
                return_value={
                    "buffer_end_arrival_sequence": 19,
                    "buffer_start_arrival_sequence": 11,
                    "cursor_expired": False,
                    "events": [{"arrival_sequence": 19, "ticker": "AAPL"}],
                    "has_more": False,
                    "next_after_arrival_sequence": 19,
                    "truncated_before": True,
                },
            ),
            patch("src.backend.app.market_event_references", return_value={}),
        ):
            payload = trading_canvas_market_events("aapl", row_limit=250)

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["snapshot_id"], "qmd-compact:AAPL:19")
        self.assertEqual(payload["last_sequence"], 19)
        self.assertIs(payload["complete"], True)
        self.assertIs(payload["truncated_before"], True)

    def test_signal_route_subscribes_before_snapshot_and_forwards_gap(self) -> None:
        entered: list[bool] = []

        def snapshot(*_args, **_kwargs):
            self.assertTrue(entered, "upstream subscription must precede snapshot capture")
            return {
                "as_of": "2026-08-11T15:00:00+00:00",
                "row_count": 1,
                "rows": [{"event_id": "signal-1", "ticker": "AAPL"}],
                "source": "qmd-gateway",
                "ticker": "AAPL",
            }

        upstream = FakeUpstream(
            [{"type": "stream_gap", "action": "resnapshot_required", "skipped": 2}]
        )
        with (
            patch(
                "src.backend.app.websockets.connect",
                return_value=FakeConnect(upstream, entered),
            ),
            patch("src.backend.app.qmd_market_signals", side_effect=snapshot),
            TestClient(app) as client,
        ):
            with client.websocket_connect(
                "/api/trading/canvas-market-signals/stream/AAPL"
            ) as websocket:
                first = websocket.receive_json()
                second = websocket.receive_json()

        self.assertEqual(first["type"], "snapshot")
        self.assertEqual(first["row_count"], 1)
        self.assertEqual(
            first["snapshot_id"], "qmd-signals:AAPL:2026-08-11T15:00:00+00:00"
        )
        self.assertEqual(second["action"], "resnapshot_required")
        self.assertEqual(second["skipped"], 2)


if __name__ == "__main__":
    unittest.main()
