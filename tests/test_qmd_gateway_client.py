from __future__ import annotations

import json
import unittest
import urllib.error
from io import BytesIO
from datetime import datetime
from unittest.mock import MagicMock
from unittest.mock import patch

from src.backend.qmd_gateway_client import (
    QmdProductRequest,
    QmdServiceError,
    normalize_qmd_macro_bar_snapshot,
    normalize_qmd_market_signal,
    qmd_compact_events,
    qmd_compact_event_page,
    qmd_computation_demand,
    qmd_computation_requirements,
    qmd_history_base_url,
    qmd_materialize_historical_watchlist_timeline,
    qmd_validate_historical_watchlist_plan,
    qmd_history_websocket_url,
    qmd_historical_scanner_snapshot,
    qmd_catalogs,
    qmd_live_market_state,
    qmd_ticker_state,
    qmd_indicators,
    qmd_market_signals,
    qmd_product_request,
    qmd_put_json,
    qmd_scanner_snapshot,
    qmd_scanner_indicators,
    qmd_websocket_url,
)
from src.request_context import begin_request_context, current_request_identity, end_request_context


class QmdGatewayClientTests(unittest.TestCase):
    @patch("src.backend.qmd_gateway_client.urllib.request.urlopen")
    @patch("src.backend.qmd_gateway_client.qmd_history_base_url", return_value="http://127.0.0.1:8801")
    def test_historical_watchlist_materialization_is_typed_and_content_bound(
        self, _base_url, urlopen
    ) -> None:
        response = MagicMock()
        response.read.return_value = (
            b'{"plan_hash":"sha256:abc","materialization_id":"sha256:def","chunks":[]}'
        )
        urlopen.return_value.__enter__.return_value = response

        payload = qmd_materialize_historical_watchlist_timeline(
            {"schema_version": 1, "plan_hash": "sha256:abc"},
            external_feature_revisions=[],
            external_feature_intervals=[],
        )

        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(
            request.full_url,
            "http://127.0.0.1:8801/materialize/watchlist-timeline",
        )
        self.assertEqual(body["plan"]["plan_hash"], "sha256:abc")
        self.assertEqual(body["external_feature_intervals"], [])
        self.assertEqual(payload["materialization_id"], "sha256:def")

    @patch("src.backend.qmd_gateway_client.urllib.request.urlopen")
    @patch("src.backend.qmd_gateway_client.qmd_history_base_url", return_value="http://127.0.0.1:8801")
    def test_historical_watchlist_plan_uses_typed_post_and_matching_hash(
        self, _base_url, urlopen
    ) -> None:
        response = MagicMock()
        response.read.return_value = b'{"valid":true,"plan_hash":"sha256:abc"}'
        urlopen.return_value.__enter__.return_value = response

        payload = qmd_validate_historical_watchlist_plan(
            {"schema_version": 1, "plan_hash": "sha256:abc"}
        )

        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(
            request.full_url,
            "http://127.0.0.1:8801/plans/watchlist-timeline/validate",
        )
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(payload["plan_hash"], "sha256:abc")

    @patch("src.backend.qmd_gateway_client.urllib.request.urlopen")
    @patch("src.backend.qmd_gateway_client.qmd_enabled", return_value=True)
    @patch("src.backend.qmd_gateway_client.qmd_base_url", return_value="http://127.0.0.1:8795")
    def test_http_transport_propagates_request_identity(
        self, _base_url, _enabled, urlopen
    ) -> None:
        response = MagicMock()
        response.read.return_value = b"{}"
        urlopen.return_value.__enter__.return_value = response
        correlation_token, causation_token, _, _ = begin_request_context(
            "web:request-17", "command:open-chart"
        )
        try:
            from src.backend.qmd_gateway_client import qmd_get_json

            qmd_get_json("/health")
        finally:
            end_request_context(correlation_token, causation_token)

        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("X-correlation-id"), "web:request-17")
        self.assertEqual(request.get_header("X-causation-id"), "command:open-chart")

    @patch("src.backend.qmd_gateway_client.qmd_enabled", return_value=True)
    @patch("src.backend.qmd_gateway_client.qmd_base_url", return_value="http://127.0.0.1:8795")
    def test_websocket_transport_propagates_request_identity_in_query(
        self, _base_url, _enabled
    ) -> None:
        correlation_token, causation_token, _, _ = begin_request_context(
            "web:request-18", None
        )
        try:
            url = qmd_websocket_url("/stream/scanner", {"limit": 25})
        finally:
            end_request_context(correlation_token, causation_token)
        self.assertIn("correlation_id=web%3Arequest-18", url)
        self.assertIn("causation_id=web%3Arequest-18", url)

    @patch("src.backend.qmd_gateway_client.qmd_history_get_json")
    def test_historical_scanner_uses_qmd_history_full_market_contract(self, get_json) -> None:
        get_json.return_value = {"ticker_count": 2, "indicators": []}

        payload = qmd_historical_scanner_snapshot(
            as_of="2026-08-07T10:15:00-04:00",
            lookback_minutes=45,
        )

        self.assertEqual(payload["ticker_count"], 2)
        path, params = get_json.call_args_list[0].args
        self.assertEqual(path, "/snapshot/scanner-derived")
        self.assertEqual(params["as_of"], "2026-08-07T14:15:00+00:00")
        self.assertEqual(params["start"], "2026-08-07T13:30:00+00:00")
        self.assertEqual(params["end"], "2026-08-07T14:15:00+00:00")
        self.assertEqual(get_json.call_args_list[1].args[0], "/source-plan")

    @patch("src.backend.qmd_gateway_client.qmd_get_json")
    def test_typed_product_request_routes_windowless_chart_to_live(self, get_json) -> None:
        get_json.return_value = {"history": []}

        response = qmd_product_request(
            QmdProductRequest("chart", ticker="aapl", timeframe="1m", limit=75)
        )

        self.assertEqual(response.authority, "live")
        self.assertEqual(response.endpoint, "/snapshot/bars/AAPL")
        get_json.assert_called_once_with(
            "/snapshot/bars/AAPL", {"timeframe": "1m", "limit": 75}, timeout=3
        )

    @patch("src.backend.qmd_gateway_client.qmd_history_get_json")
    def test_typed_product_request_routes_causal_window_to_history(self, get_json) -> None:
        get_json.return_value = {"bars": [], "complete": True}

        response = qmd_product_request(
            QmdProductRequest(
                "chart",
                ticker="aapl",
                timeframe="1m",
                start="2026-08-08T08:00:00-04:00",
                end="2026-08-08T20:00:00-04:00",
                as_of="2026-08-08T12:00:00-04:00",
                stage="bars",
                limit=80,
                timeout_seconds=12,
            )
        )

        self.assertEqual(response.authority, "history")
        self.assertEqual(response.schema_version, 2)
        self.assertEqual(response.endpoint, "/snapshot/chart-bars/AAPL")
        params = get_json.call_args_list[0].args[1]
        self.assertEqual(params["stage"], "bars")
        self.assertEqual(params["as_of"], "2026-08-08T12:00:00-04:00")
        self.assertEqual(get_json.call_args_list[0].kwargs["timeout"], 12)
        self.assertEqual(get_json.call_args_list[1].args[0], "/source-plan")

    @patch("src.backend.qmd_gateway_client.urllib.request.urlopen")
    @patch("src.backend.qmd_gateway_client.qmd_enabled", return_value=True)
    @patch("src.backend.qmd_gateway_client.qmd_base_url", return_value="http://127.0.0.1:8795")
    def test_http_transport_raises_typed_retryable_error(
        self, _base_url, _enabled, urlopen
    ) -> None:
        urlopen.side_effect = urllib.error.URLError("connection refused")
        from src.backend.qmd_gateway_client import qmd_get_json

        with self.assertRaises(QmdServiceError) as raised:
            qmd_get_json("/snapshot/scanner")

        self.assertEqual(raised.exception.code, "qmd_upstream_unavailable")
        self.assertEqual(raised.exception.path, "/snapshot/scanner")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.as_detail()["service"], "QMD")

    @patch("src.backend.qmd_gateway_client.qmd_enabled", return_value=False)
    def test_disabled_gateway_is_a_typed_non_retryable_error(self, _enabled) -> None:
        from src.backend.qmd_gateway_client import qmd_get_json

        with self.assertRaises(QmdServiceError) as raised:
            qmd_get_json("/health")

        self.assertEqual(raised.exception.code, "qmd_disabled")
        self.assertFalse(raised.exception.retryable)

    @patch("src.backend.qmd_gateway_client.urllib.request.urlopen")
    @patch("src.backend.qmd_gateway_client.qmd_enabled", return_value=True)
    @patch("src.backend.qmd_gateway_client.qmd_base_url", return_value="http://127.0.0.1:8795")
    def test_mutation_transport_raises_typed_http_error(
        self, _base_url, _enabled, urlopen
    ) -> None:
        urlopen.side_effect = urllib.error.HTTPError(
            "http://127.0.0.1:8795/computation-targets",
            429,
            "Too Many Requests",
            {},
            BytesIO(b'{"error":"capacity"}'),
        )

        with self.assertRaises(QmdServiceError) as raised:
            qmd_put_json("/computation-targets", {"target_id": "chart:aapl"})

        self.assertEqual(raised.exception.operation, "PUT")
        self.assertEqual(raised.exception.upstream_status, 429)
        self.assertTrue(raised.exception.retryable)

    @patch("src.backend.qmd_gateway_client.qmd_history_get_json")
    def test_typed_product_response_projects_standard_metadata(self, get_json) -> None:
        get_json.return_value = {
            "bars": [],
            "complete": False,
            "coverage": {"status": "partial"},
            "source_revision": {"token": "rev-17"},
            "warnings": [{"code": "archive_pending", "message": "Archive segment is pending."}],
        }

        response = qmd_product_request(
            QmdProductRequest(
                "chart",
                ticker="AAPL",
                start="2026-08-08T08:00:00-04:00",
                end="2026-08-08T20:00:00-04:00",
            )
        )

        self.assertFalse(response.complete)
        self.assertEqual(response.coverage_status, "partial")
        self.assertEqual(response.source_revision, "rev-17")
        self.assertEqual(response.warnings, ("Archive segment is pending.",))

    def test_compact_event_window_trusts_qmd_history_native_live_continuation(self) -> None:
        boundary_us = int(datetime.fromisoformat("2026-08-08T20:00:00+00:00").timestamp() * 1_000_000)
        historical = [
            {
                "ticker": "AAPL",
                "sip_timestamp_us": boundary_us - 1_000_000,
                "source_sequence": 1,
                "event_meta": 1,
                "arrival_sequence": 8,
            },
            {
                "ticker": "AAPL",
                "sip_timestamp_us": boundary_us + 20_000_000,
                "source_sequence": 3,
                "event_meta": 1,
                "arrival_sequence": 10,
            },
        ]

        def history_get(path, params, *, timeout):
            return historical

        live_calls = []

        def live_get(path, params, *, timeout):
            live_calls.append((path, params, timeout))
            raise AssertionError("backend must not re-read a QMD-native live continuation")

        response = qmd_product_request(
            QmdProductRequest(
                "compact_events",
                authority="history",
                ticker="aapl",
                start="2026-08-08T19:59:00+00:00",
                end="2026-08-08T20:01:00+00:00",
                limit=10,
                tail=True,
            ),
            history_get=history_get,
            live_get=live_get,
        )

        self.assertEqual([row["source_sequence"] for row in response.payload], [1, 3])
        self.assertEqual(live_calls, [])

    def test_chart_window_trusts_qmd_history_native_live_continuation(self) -> None:
        plan = {
            "plan_hash": "plan-1",
            "segments": [
                {
                    "tier": "current_live",
                    "start": "2026-08-08T20:00:00+00:00",
                    "end": "2026-08-08T20:02:00+00:00",
                }
            ],
        }
        historical = {
            "bars": [
                {"bar_start": "2026-08-08T19:59:00+00:00", "sym": "AAPL", "timeframe": "1m"},
                {"bar_start": "2026-08-08T20:00:00+00:00", "sym": "AAPL", "timeframe": "1m"},
                {"bar_start": "2026-08-08T20:01:00+00:00", "sym": "AAPL", "timeframe": "1m"},
            ],
            "indicators": [],
            "cache": {"source_revision": {
                "live_continuation_sequence": 42,
                "request_complete": True,
                "token": "rev-live-42",
            }},
            "ticker": "AAPL",
            "timeframe": "1m",
        }

        def history_get(path, params, *, timeout):
            return plan if path == "/source-plan" else historical

        def live_get(path, params, *, timeout):
            raise AssertionError("backend must not duplicate QMD History computation")

        response = qmd_product_request(
            QmdProductRequest(
                "chart",
                authority="history",
                ticker="AAPL",
                timeframe="1m",
                start="2026-08-08T19:59:00+00:00",
                end="2026-08-08T20:02:00+00:00",
            ),
            history_get=history_get,
            live_get=live_get,
        )

        self.assertEqual(
            [row["bar_start"] for row in response.payload["bars"]],
            [
                "2026-08-08T19:59:00+00:00",
                "2026-08-08T20:00:00+00:00",
                "2026-08-08T20:01:00+00:00",
            ],
        )
        self.assertEqual(len(response.payload["indicators"]), 0)
        self.assertTrue(response.complete)
        self.assertEqual(response.coverage_status, "complete_with_live_continuation")

    def test_scanner_window_trusts_qmd_history_native_live_continuation(self) -> None:
        plan = {
            "segments": [
                {
                    "tier": "current_live",
                    "start": "2026-08-08T20:00:00+00:00",
                    "end": "2026-08-08T20:02:00+00:00",
                }
            ]
        }
        historical = {
            "indicator_timeframe": "100ms",
            "indicators": [
                {"bar_start": "2026-08-08T20:01:00+00:00", "sym": "AAPL", "timeframe": "100ms"}
            ],
            "active_signals": [{"event_id": "active-1", "detected_at": "2026-08-08T20:01:10+00:00"}],
            "recent_signal_events": [{"event_id": "event-1", "detected_at": "2026-08-08T20:01:20+00:00"}],
            "source_revision": {
                "live_continuation_sequence": 44,
                "request_complete": True,
                "token": "rev-live-44",
            },
            "ticker_count": 1,
        }

        def history_get(path, params, *, timeout):
            return plan if path == "/source-plan" else historical

        def live_get(path, params, *, timeout):
            raise AssertionError("backend must not duplicate QMD History computation")

        response = qmd_product_request(
            QmdProductRequest(
                "scanner",
                authority="history",
                start="2026-08-08T19:59:00+00:00",
                end="2026-08-08T20:02:00+00:00",
            ),
            history_get=history_get,
            live_get=live_get,
        )

        self.assertEqual(response.payload["ticker_count"], 1)
        self.assertEqual(response.payload["indicators"][0]["bar_start"], "2026-08-08T20:01:00+00:00")
        self.assertEqual(response.payload["active_signals"][0]["event_id"], "active-1")
        self.assertEqual(response.payload["recent_signal_events"][0]["event_id"], "event-1")
        self.assertTrue(response.complete)

    def test_typed_product_request_rejects_ambiguous_or_naive_windows(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot carry a historical window"):
            qmd_product_request(
                QmdProductRequest(
                    "chart",
                    authority="live",
                    ticker="AAPL",
                    start="2026-08-08T08:00:00-04:00",
                    end="2026-08-08T20:00:00-04:00",
                )
            )
        with self.assertRaisesRegex(ValueError, "must include a timezone"):
            qmd_product_request(
                QmdProductRequest(
                    "compact_events",
                    ticker="AAPL",
                    start="2026-08-08T08:00:00",
                    end="2026-08-08T20:00:00",
                )
            )

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
    def test_catalog_fanout_preserves_request_lineage_in_workers(self, get_json) -> None:
        observed: list[dict[str, str]] = []

        def response(path, *, timeout):
            observed.append(current_request_identity())
            return [{"key": path.strip("/")}]

        get_json.side_effect = response
        tokens = begin_request_context("web:catalog-9", "view:discovery")
        try:
            qmd_catalogs()
        finally:
            end_request_context(tokens[0], tokens[1])
        self.assertEqual(len(observed), 3)
        self.assertTrue(
            all(
                identity == {
                    "correlation_id": "web:catalog-9",
                    "causation_id": "view:discovery",
                }
                for identity in observed
            )
        )

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

    @patch("src.backend.qmd_gateway_client.qmd_get_json")
    def test_ticker_state_uses_versioned_live_memory_envelope(self, get_json) -> None:
        get_json.return_value = {
            "schema_version": 1,
            "authority": "qmd_gateway_live_memory",
            "ticker": "AAPL",
            "found": True,
            "sequence": 14,
            "row": {"last_price": 101.5},
        }

        self.assertEqual(qmd_ticker_state("aapl"), get_json.return_value)
        get_json.assert_called_once_with("/snapshot/ticker-state/AAPL", timeout=3)

    @patch("src.backend.qmd_gateway_client.qmd_get_json")
    def test_computation_demand_uses_qmd_target_authority(self, get_json) -> None:
        get_json.return_value = {
            "active_target_count": 2,
            "active_symbol_count": 11,
            "estimated_demand_units": 176,
        }

        self.assertEqual(qmd_computation_demand(), get_json.return_value)
        get_json.assert_called_once_with("/computation-targets", timeout=3)

    def test_computation_requirements_preserve_live_and_history_authorities(self) -> None:
        live_get = MagicMock(
            return_value={
                "requirements": [
                    {
                        "requirement_id": "live-1",
                        "ticker": "AAPL",
                        "source_revision": "advancing_live",
                    }
                ]
            }
        )
        history_get = MagicMock(
            return_value={
                "requirements": [
                    {
                        "requirement_id": "history-1",
                        "ticker": "AAPL",
                        "source_revision": "revision-17",
                    }
                ]
            }
        )

        payload = qmd_computation_requirements(
            live_get=live_get,
            history_get=history_get,
        )

        self.assertTrue(payload["complete"])
        self.assertEqual(payload["active_requirement_count"], 2)
        self.assertEqual(payload["live_requirement_count"], 1)
        self.assertEqual(payload["offline_requirement_count"], 1)
        self.assertEqual(
            {row["authority"] for row in payload["requirements"]},
            {"qmd_gateway", "qmd_history"},
        )
        live_get.assert_called_once_with("/computation-targets", timeout=3)
        history_get.assert_called_once_with("/snapshot/cache", timeout=3)

    def test_computation_requirements_degrades_one_authority_explicitly(self) -> None:
        def unavailable(*_args, **_kwargs):
            raise RuntimeError("history offline")

        payload = qmd_computation_requirements(
            live_get=lambda *_args, **_kwargs: {"requirements": []},
            history_get=unavailable,
        )

        self.assertFalse(payload["complete"])
        self.assertEqual(payload["authorities"]["qmd_history"], "unavailable")
        self.assertIn("history offline", payload["errors"]["qmd_history"])

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
        self.assertEqual(lease["correlation_id"], "run:chart:AAPL:1m")
        self.assertEqual(lease["causation_id"], "event:chart-request:AAPL:1m")
        get_json.assert_called_once_with(
            "/snapshot/indicators/AAPL",
            {"timeframe": "1m", "limit": 50},
            timeout=3,
        )

    @patch("src.backend.qmd_gateway_client.qmd_get_json")
    def test_compact_events_preserve_only_object_rows(self, get_json) -> None:
        get_json.return_value = {
            "schema_version": 4,
            "ticker": "AAPL",
            "events": [{"ticker": "AAPL", "arrival_sequence": 7}, "invalid", None],
        }

        self.assertEqual(qmd_compact_events("aapl", row_limit=50), [{"ticker": "AAPL", "arrival_sequence": 7}])
        get_json.assert_called_once_with(
            "/snapshot/compact-event-page/AAPL",
            {"limit": 50, "after_arrival_sequence": None},
            timeout=3,
        )

    @patch("src.backend.qmd_gateway_client.qmd_get_json")
    def test_compact_event_page_preserves_cursor_and_eviction_evidence(self, get_json) -> None:
        get_json.return_value = {
            "schema_version": 4,
            "ticker": "AAPL",
            "cursor_expired": True,
            "next_after_arrival_sequence": 42,
            "events": [{"ticker": "AAPL", "arrival_sequence": 42}],
        }

        page = qmd_compact_event_page(
            "aapl", after_arrival_sequence=17, row_limit=25
        )

        self.assertTrue(page["cursor_expired"])
        self.assertEqual(page["next_after_arrival_sequence"], 42)
        get_json.assert_called_once_with(
            "/snapshot/compact-event-page/AAPL",
            {"limit": 25, "after_arrival_sequence": 17},
            timeout=3,
        )

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

    @patch("src.backend.qmd_gateway_client.load_qmd_env")
    def test_history_base_url_resolves_wildcard_bind_to_loopback(self, _load_env) -> None:
        with patch.dict(
            "os.environ",
            {"QMD_HISTORY_BIND": "0.0.0.0:8801", "QMD_HISTORY_GATEWAY_URL": ""},
            clear=False,
        ):
            self.assertEqual(qmd_history_base_url(), "http://127.0.0.1:8801")

    @patch("src.backend.qmd_gateway_client.qmd_history_base_url", return_value="https://history.example.test/base")
    def test_history_websocket_uses_shared_url_contract(self, _base_url) -> None:
        self.assertEqual(
            qmd_history_websocket_url("stream/events", {"start": "2026-08-08T08:00:00Z"}),
            "wss://history.example.test/base/stream/events?start=2026-08-08T08%3A00%3A00Z",
        )


if __name__ == "__main__":
    unittest.main()
