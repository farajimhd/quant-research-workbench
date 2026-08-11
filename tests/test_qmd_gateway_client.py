from __future__ import annotations

import unittest
from unittest.mock import patch

from src.backend.qmd_gateway_client import (
    normalize_qmd_macro_bar_snapshot,
    normalize_qmd_market_signal,
    qmd_compact_events,
    qmd_catalogs,
    qmd_live_market_state,
    qmd_indicators,
    qmd_market_signals,
    qmd_scanner_snapshot,
    qmd_scanner_indicators,
    qmd_websocket_url,
)


class QmdGatewayClientTests(unittest.TestCase):
    @patch("src.backend.qmd_gateway_client.qmd_get_json")
    def test_catalog_bundle_includes_canonical_computation_scope(self, get_json) -> None:
        get_json.side_effect = lambda path, *, timeout: [{"key": path.strip("/")}]

        payload = qmd_catalogs()

        self.assertEqual(payload["authority"], "qmd_runtime_catalog")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(len(payload["content_hash"]), 64)
        self.assertEqual(payload["capability_catalog"], [{"key": "capability-catalog"}])
        self.assertEqual(
            {call.args[0] for call in get_json.call_args_list},
            {"/capability-catalog", "/indicator-catalog", "/signal-catalog"},
        )
        self.assertTrue(all(call.kwargs["timeout"] == 3 for call in get_json.call_args_list))

    @patch("src.backend.qmd_gateway_client.qmd_get_json")
    def test_market_signal_snapshot_filters_symbol_without_recomputing_signals(
        self, get_json
    ) -> None:
        get_json.return_value = {
            "as_of": "2026-07-17T13:45:01Z",
            "rows": [
                {
                    "event_id": "event-a",
                    "signal_id": "signal-a",
                    "signal_key": "vwap_transition",
                    "state": "triggered",
                    "ticker": "AAPL",
                    "working_timeframe": "1s",
                    "direction": "bullish",
                    "score": 0.72,
                    "rank_score": 0.67,
                    "confidence": 0.81,
                    "effective_at": "2026-07-17T13:45:00.100Z",
                },
                {
                    "event_id": "event-b",
                    "signal_id": "signal-b",
                    "signal_key": "price_volume_expansion",
                    "state": "triggered",
                    "ticker": "MSFT",
                    "working_timeframe": "1s",
                    "direction": "bearish",
                    "score": -0.62,
                    "rank_score": 0.59,
                    "confidence": 0.71,
                    "effective_at": "2026-07-17T13:45:00.200Z",
                },
            ],
        }

        payload = qmd_market_signals("aapl", include_history=True, row_limit=20)

        self.assertEqual(payload["ticker"], "AAPL")
        self.assertEqual(payload["mode"], "lifecycle_history")
        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(payload["rows"][0]["signal_event_id"], "event-a")
        get_json.assert_called_once_with(
            "/snapshot/signal-events", {"limit": 20}, timeout=3
        )

    def test_market_signal_normalizer_preserves_lifecycle_identity(self) -> None:
        row = normalize_qmd_market_signal(
            {
                "event_id": "event-2",
                "signal_id": "signal-1",
                "signal_key": "vwap_transition",
                "state": "updated",
                "ticker": "AAPL",
                "working_timeframe": "1s",
                "direction": "bullish",
                "score": 0.72,
                "rank_score": 0.67,
                "confidence": 0.81,
                "effective_at": "2026-07-17T13:45:00.100Z",
            }
        )

        self.assertEqual(row["signal_event_id"], "event-2")
        self.assertEqual(row["signal_id"], "signal-1")
        self.assertEqual(row["signal_state"], "updated")
        self.assertEqual(row["signal_rank_score"], 0.67)
        self.assertEqual(row["signal_confidence"], 0.81)
        self.assertEqual(row["signal_domain"], "market")
        self.assertEqual(row["signal_producer"], "qmd")
        self.assertEqual(row["input_basis"], "bar_derived")
        self.assertEqual(row["publication_cadence"], "bar_close")

    @patch("src.backend.qmd_gateway_client.qmd_get_json")
    def test_scanner_keeps_market_universe_and_joins_active_signal_state(
        self, get_json
    ) -> None:
        def response(path, params=None, *, timeout=3):
            self.assertEqual(timeout, 3)
            if path == "/snapshot/scanner":
                return {
                    "rows": [
                        {"ticker": "AAPL", "price": 315.0},
                        {"ticker": "MSFT", "price": 500.0},
                    ],
                    "as_of": "2026-07-17T13:45:01Z",
                }
            if path == "/snapshot/signals":
                return {
                    "rows": [
                        {
                            "event_id": "active-a",
                            "signal_id": "signal-a",
                            "signal_key": "vwap_transition",
                            "state": "triggered",
                            "ticker": "AAPL",
                            "working_timeframe": "1s",
                            "direction": "bearish",
                            "score": -0.95,
                            "rank_score": 0.74,
                            "confidence": 0.60,
                            "effective_at": "2026-07-17T13:45:00.100Z",
                        },
                        {
                            "event_id": "active-b",
                            "signal_id": "signal-b",
                            "signal_key": "price_volume_expansion",
                            "state": "updated",
                            "ticker": "AAPL",
                            "working_timeframe": "1s",
                            "direction": "bullish",
                            "score": 0.80,
                            "rank_score": 0.91,
                            "confidence": 0.90,
                            "effective_at": "2026-07-17T13:45:00.200Z",
                        },
                    ]
                }
            if path == "/snapshot/signal-events":
                return {
                    "rows": [
                        {
                            "event_id": "resolved-c",
                            "signal_id": "signal-c",
                            "signal_key": "price_volume_expansion",
                            "state": "resolved",
                            "ticker": "MSFT",
                            "working_timeframe": "1s",
                            "direction": "bearish",
                            "score": -0.55,
                            "rank_score": 0.63,
                            "confidence": 0.75,
                            "effective_at": "2026-07-17T13:44:59.900Z",
                        }
                    ]
                }
            if path == "/snapshot/scanner-indicators":
                return {
                    "rows": [
                        {
                            "sym": "AAPL",
                            "timeframe": "10s",
                            "bar_end": "2026-07-17T13:45:00Z",
                            "flow_structure_composite_score": -0.4,
                            "flow_structure_composite_confidence": 0.8,
                        }
                    ]
                }
            self.fail(f"Unexpected QMD route: {path}")

        get_json.side_effect = response
        payload = qmd_scanner_snapshot(
            row_limit=25,
            enrichments={"indicators", "signals", "signal_events"},
        )

        self.assertEqual([row["ticker"] for row in payload["rows"]], ["AAPL", "MSFT"])
        self.assertEqual(payload["rows"][0]["signal_id"], "signal-b")
        self.assertEqual(payload["rows"][0]["signal_rank_score"], 0.91)
        self.assertEqual(payload["rows"][0]["active_signal_count"], 2)
        self.assertEqual(payload["rows"][0]["flow_structure_composite_score"], -0.4)
        self.assertEqual(payload["rows"][0]["indicator_timeframe"], "10s")
        self.assertEqual(payload["rows"][0]["indicator_type"], "qmd")
        self.assertEqual(payload["rows"][0]["indicator_input_basis"], "event_native")
        self.assertEqual(payload["rows"][0]["indicator_publication_cadence"], "bar_close")
        self.assertNotIn("signal_id", payload["rows"][1])
        self.assertEqual(payload["signal_rows"][0]["signal_event_id"], "resolved-c")
        self.assertEqual(payload["signal_row_count"], 1)
        self.assertEqual(
            payload["included_enrichments"],
            ["indicators", "signal_events", "signals"],
        )

    @patch("src.backend.qmd_gateway_client.qmd_get_json")
    def test_core_scanner_does_not_fetch_watchlist_computations(self, get_json) -> None:
        get_json.return_value = {
            "rows": [{"ticker": "AAPL", "price": 315.0}],
            "as_of": "2026-07-17T13:45:01Z",
        }

        payload = qmd_scanner_snapshot(row_limit=25)

        get_json.assert_called_once_with(
            "/snapshot/scanner", {"limit": 25}, timeout=3
        )
        self.assertEqual(payload["computation_scope"], "core_scan")
        self.assertEqual(payload["included_enrichments"], [])
        self.assertEqual(payload["signal_rows"], [])
        self.assertNotIn("indicator_type", payload["rows"][0])

    def test_scanner_rejects_unknown_enrichment_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported QMD scanner enrichment"):
            qmd_scanner_snapshot(enrichments={"everything"})

    @patch("src.backend.qmd_gateway_client.qmd_get_json")
    def test_scanner_indicator_projection_is_explicit_and_focused(self, get_json) -> None:
        get_json.return_value = {
            "rows": [{"sym": "AAPL", "timeframe": "1s", "vwap": 101.5}]
        }

        rows = qmd_scanner_indicators(timeframe="1s", row_limit=99_999)

        self.assertEqual(rows[0]["ticker"], "AAPL")
        self.assertEqual(rows[0]["vwap"], 101.5)
        get_json.assert_called_once_with(
            "/snapshot/scanner-indicators",
            {"limit": 5_000, "timeframe": "1s"},
            timeout=3,
        )

    @patch("src.backend.qmd_gateway_client.qmd_get_json")
    def test_live_market_state_uses_symbol_snapshot(self, get_json) -> None:
        get_json.return_value = {"ticker": "AAPL", "is_live_tradable": True}

        self.assertEqual(qmd_live_market_state("aapl"), get_json.return_value)
        get_json.assert_called_once_with("/snapshot/live-market-state/AAPL", timeout=3)

    @patch("src.backend.qmd_gateway_client.qmd_put_json")
    @patch("src.backend.qmd_gateway_client.qmd_get_json")
    def test_indicator_request_leases_focused_chart_computation(
        self, get_json, put_json
    ) -> None:
        get_json.return_value = {
            "ticker": "AAPL",
            "timeframe": "1m",
            "history": [],
            "current": None,
        }

        payload = qmd_indicators("aapl", timeframe="1m", row_limit=50)

        self.assertEqual(payload["ticker"], "AAPL")
        lease = put_json.call_args.args[1]
        self.assertEqual(lease["target_id"], "chart:AAPL:1m")
        self.assertEqual(lease["scope"], "request")
        self.assertEqual(lease["tickers"], ["AAPL"])
        self.assertEqual(lease["timeframes"], ["1m"])
        self.assertEqual(lease["ttl_seconds"], 300)
        get_json.assert_called_once_with(
            "/snapshot/indicators/AAPL",
            {"timeframe": "1m", "limit": 50},
            timeout=3,
        )

    @patch("src.backend.qmd_gateway_client.qmd_get_json")
    def test_compact_events_preserve_only_object_rows(self, get_json) -> None:
        get_json.return_value = [{"ticker": "AAPL", "arrival_sequence": 7}, "invalid", None]

        self.assertEqual(qmd_compact_events("aapl", row_limit=50), [{"ticker": "AAPL", "arrival_sequence": 7}])
        get_json.assert_called_once_with("/snapshot/compact-events/AAPL", {"limit": 50}, timeout=3)

    def test_macro_snapshot_projects_trade_family_and_current_bar(self) -> None:
        result = normalize_qmd_macro_bar_snapshot(
            {
                "rows": [
                    {"bar_family": "quote", "bar_start": "2026-07-01T00:00:00Z"},
                    {
                        "bar_family": "trade",
                        "bar_start": "2026-07-01T00:00:00Z",
                        "bar_end": "2026-08-01T00:00:00Z",
                        "close": 315.0,
                        "high": 320.0,
                        "local_date": "2026-07-01",
                        "low": 300.0,
                        "open": 305.0,
                        "size_sum": 10_000.0,
                        "state": "closed",
                    },
                    {
                        "bar_family": "trade",
                        "bar_start": "2026-08-01T00:00:00Z",
                        "bar_end": "2026-09-01T00:00:00Z",
                        "close": 321.0,
                        "high": 322.0,
                        "local_date": "2026-08-01",
                        "low": 314.0,
                        "open": 315.0,
                        "size_sum": 2_500.0,
                        "state": "partial",
                    },
                ],
            },
            symbol="AAPL",
            timeframe="1mo",
        )

        self.assertEqual(len(result["history"]), 1)
        self.assertEqual(result["history"][0]["timeframe"], "1mo")
        self.assertTrue(result["history"][0]["is_closed"])
        self.assertEqual(result["current"]["close"], 321.0)
        self.assertFalse(result["current"]["is_closed"])

    @patch("src.backend.qmd_gateway_client.qmd_enabled", return_value=True)
    @patch("src.backend.qmd_gateway_client.qmd_base_url", return_value="http://127.0.0.1:8795")
    def test_websocket_url_uses_qmd_authority_and_query(self, _base_url, _enabled) -> None:
        self.assertEqual(
            qmd_websocket_url("/stream/bars/AAPL", {"timeframe": "1m", "limit": 500}),
            "ws://127.0.0.1:8795/stream/bars/AAPL?timeframe=1m&limit=500",
        )

    @patch("src.backend.qmd_gateway_client.qmd_enabled", return_value=True)
    @patch("src.backend.qmd_gateway_client.qmd_base_url", return_value="https://qmd.example.test/base")
    def test_websocket_url_uses_tls_for_https(self, _base_url, _enabled) -> None:
        self.assertEqual(
            qmd_websocket_url("stream/indicators/MSFT", {"timeframe": "5m"}),
            "wss://qmd.example.test/base/stream/indicators/MSFT?timeframe=5m",
        )


if __name__ == "__main__":
    unittest.main()
