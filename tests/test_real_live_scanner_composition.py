from __future__ import annotations

import threading
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from src.backend.bounded_cache import BoundedSingleFlightTtlCache
from src.backend import real_live_trading_service as service
from src.backend.trading_configuration_service import _default_draft
from src.trading_runtime.journal import TradingJournal


class RealLiveScannerCompositionTests(unittest.TestCase):
    def test_signal_stream_materialization_publishes_qmd_recovery_graph(self) -> None:
        configuration = _default_draft()
        at = datetime(2026, 8, 20, 15, 0, tzinfo=UTC)
        with patch.object(
            service, "qmd_configure_signal_streams", side_effect=lambda payload: payload
        ):
            payload = service.materialize_qmd_signal_stream_configuration(
                configuration, as_of=at
            )

        recovery = {
            row["signal_stream_id"]: row
            for row in payload["recovery_templates"]
        }
        self.assertEqual(
            recovery["price-squeeze-5m"]["recovery_kind"],
            "source_native",
        )
        self.assertEqual(
            recovery["price-squeeze-early"]["recovery_kind"],
            "source_native",
        )
        self.assertEqual(recovery["market-halts"]["recovery_kind"], "source_native")
        self.assertTrue(payload["configuration_revision"].startswith("sha256:"))

    def test_native_halt_hydration_reconstructs_session_and_is_idempotent(self) -> None:
        configuration = _default_draft()
        configuration["market_discovery"]["signal_streams"] = [
            stream
            for stream in configuration["market_discovery"]["signal_streams"]
            if stream["signal_stream_id"] == "market-halts"
        ]
        at = datetime(2026, 8, 20, 15, 0, tzinfo=UTC)
        source = {
            "complete": True,
            "rows": [{
                "event_id": "qmd-halt-open-1",
                "ticker": "HALT",
                "event_start_utc": "2026-08-20T14:15:00+00:00",
                "source_event_ts_utc": "2026-08-20T14:15:00+00:00",
                "source_event_type": "quote",
                "event_status": "opened",
                "source_conditions": [43],
                "source_indicators": [],
                "block_reason": "quote_condition_halt",
                "evidence_json": '{"bid": 10.0, "ask": 10.1}',
                "source_run_id": "qmd-live-1",
            }, {
                "event_id": "qmd-halt-update-1",
                "ticker": "HALT",
                "event_start_utc": "2026-08-20T14:15:00+00:00",
                "source_event_ts_utc": "2026-08-20T14:15:01+00:00",
                "source_event_type": "quote",
                "event_status": "updated",
                "source_conditions": [43],
                "source_indicators": [],
                "block_reason": "quote_condition_halt",
                "evidence_json": '{"bid": 9.9, "ask": 10.0}',
                "source_run_id": "qmd-live-1",
            }],
        }
        seen_source_ids: set[str] = set()

        def append_once(_stream_id: str, rows: list[dict]) -> dict:
            fresh = [
                row
                for row in rows
                if str(row.get("source_event_id") or "") not in seen_source_ids
            ]
            seen_source_ids.update(
                str(row.get("source_event_id") or "") for row in fresh
            )
            return {"new_occurrences": fresh}

        with tempfile.TemporaryDirectory() as temporary:
            journal = TradingJournal(Path(temporary) / "journal.sqlite3")
            try:
                with patch.object(
                    service,
                    "qmd_append_signal_stream_rows",
                    side_effect=append_once,
                ):
                    first = service.hydrate_native_signal_streams(
                        configuration,
                        as_of=at,
                        journal=journal,
                        history_loader=lambda **_: source,
                        scanner_rows=[{
                            "ticker": "HALT",
                            "last_price": 9.8,
                            "data.price_change_5_bar_pct@1:value@@1m": -2.5,
                        }],
                        force=True,
                    )
                    second = service.hydrate_native_signal_streams(
                        configuration,
                        as_of=at,
                        journal=journal,
                        history_loader=lambda **_: source,
                        force=True,
                    )
            finally:
                journal.close()

        self.assertEqual(first["source_row_count"], 2)
        self.assertEqual(first["inserted_count"], 2)
        self.assertEqual(first["new_occurrences"][0]["ticker"], "HALT")
        occurrence = first["new_occurrences"][0]
        self.assertEqual(occurrence["last_price"], 9.8)
        self.assertEqual(occurrence["halt_direction"], "Down")
        self.assertIn("LULD", occurrence["halt_category"])
        self.assertEqual(first["new_occurrences"][1]["bid_price"], 9.9)
        self.assertEqual(service._halt_occurrence_row(source["rows"][0])["bid"], 10.0)
        self.assertEqual(second["inserted_count"], 0)

    def test_halt_category_decodes_raw_codes_with_their_source_family(self) -> None:
        luld = service._halt_category({
            "source_event_type": "luld",
            "source_indicators": [17],
            "source_conditions": [],
        })
        quote = service._halt_category({
            "source_event_type": "quote",
            "source_indicators": [],
            "source_conditions": [17],
        })

        self.assertEqual(luld, "Suspended Halt Pause")
        self.assertEqual(quote, "Fast Trading")

    def test_live_halt_snapshot_recovers_causal_price_and_five_minute_change(self) -> None:
        calls = 0

        def history_loader(*_args, **_kwargs) -> dict:
            nonlocal calls
            calls += 1
            return {
                "bars": [
                    {
                        "close": 10.0,
                        "last_event_timestamp_us": 1_787_324_400_000_000,
                    },
                    {
                        "close": 9.0,
                        "last_event_timestamp_us": 1_787_324_690_000_000,
                    },
                ]
            }

        row = {
            "event_id": "legacy-halt-without-market-context",
            "event_time": "2026-08-21T15:05:00+00:00",
            "halt_category": "Closed",
            "halt_direction": "Unavailable",
            "last_price": None,
            "field__price__change__5__bar__pct": None,
            "signal_stream_id": "market-halts",
            "sequence": 2,
            "ticker": "HALTCTX",
        }
        stale_revision = {**row, "halt_category": None, "sequence": 1}
        result = service.enrich_halt_signal_stream_snapshot(
            {"occurrences": [stale_revision, row], "new_occurrences": [row]},
            history_loader=history_loader,
        )

        occurrence = result["occurrences"][0]
        self.assertEqual(len(result["occurrences"]), 1)
        self.assertEqual(calls, 1)
        self.assertEqual(occurrence["last_price"], 9.0)
        self.assertAlmostEqual(occurrence["field__price__change__5__bar__pct"], -10.0)
        self.assertEqual(occurrence["halt_direction"], "Down")
        self.assertEqual(occurrence["halt_category"], "Suspended Halt Pause")
        self.assertTrue(occurrence["halt_market_context_recovered"])

    def test_native_halt_evidence_precedes_later_scanner_fallback(self) -> None:
        occurrence = service._halt_occurrence_row(
            {
                "event_id": "qmd-halt-open-durable",
                "ticker": "HALT",
                "source_event_ts_utc": "2026-08-20T14:15:00+00:00",
                "source_conditions": [45],
                "evidence_json": '{"last_price": 9.8, "change_5m_pct": -2.5}',
            },
            scanner_row={
                "last_price": 11.0,
                "price_change_5_bar_pct": 4.0,
            },
        )

        self.assertEqual(occurrence["last_price"], 9.8)
        self.assertEqual(occurrence["halt_change_5m_pct"], -2.5)
        self.assertEqual(occurrence["halt_direction"], "Down")

    def test_interval_demand_prefers_qmd_source_names_over_projection_names(self) -> None:
        selected = service.qmd_interval_runtime_field(
            {"field_ref": "data.price_change@1:value", "aggregation": ""},
            {
                "data.price_change@1:value": {
                    "source_id": "price_change_1_bar_pct",
                    "runtime_field": "field__price__change__1__bar__pct",
                },
            },
        )

        self.assertEqual(selected, "price_change_1_bar_pct")

    def test_interval_sources_load_concurrently_and_preserve_configuration_order(self) -> None:
        barrier = threading.Barrier(3, timeout=1)

        def indicator_loader(*, timeframe: str, row_limit: int, fields):
            self.assertEqual(row_limit, 25_000)
            self.assertEqual(fields, {"price_change_pct"})
            barrier.wait()
            return [{"ticker": timeframe.upper()}]

        def macro_loader(*, timeframe: str, row_limit: int):
            self.assertEqual(row_limit, 25_000)
            barrier.wait()
            return [{"ticker": timeframe.upper()}]

        rows = service.load_discovery_interval_sources(
            ("5m", "1d", "100ms"),
            indicator_loader=indicator_loader,
            macro_loader=macro_loader,
            indicator_fields={"price_change_pct"},
        )

        self.assertEqual([interval for interval, _ in rows], ["5m", "1d", "100ms"])
        self.assertEqual([source[0]["ticker"] for _, source in rows], ["5M", "1D", "100MS"])

    def test_interval_merge_never_replaces_session_fields_with_raw_bar_values(self) -> None:
        merged = service.merge_interval_field_instances(
            {"volume": 6_800_000.0, "vwap": 340.25},
            {
                "ticker": "TSLA",
                "volume": 5_456.0,
                "vwap": 338.97,
                "data.price_change_pct@1:value@@3m": 0.25,
            },
        )
        self.assertEqual(merged["volume"], 6_800_000.0)
        self.assertEqual(merged["vwap"], 340.25)
        self.assertEqual(merged["data.price_change_pct@1:value@@3m"], 0.25)

    def test_signal_candidates_materialize_exact_configured_field_instances(self) -> None:
        discovery = {
            "column_catalog": [
                {
                    "column_id": "event_quote_bid_price",
                    "field_ref": "data.quote.bid_price@1:value",
                },
                {
                    "column_id": "event_quote_ask_price",
                    "field_ref": "data.quote.ask_price@1:value",
                },
                {
                    "column_id": "field__price__change__1__bar__pct",
                    "field_ref": "data.price_change_1_bar_pct@1:value",
                },
                {
                    "column_id": "field__volume__rate__ratio",
                    "field_ref": "data.volume_rate_ratio@1:value",
                },
            ],
            "rule_sets": [],
            "signal_streams": [
                {
                    "columns": [
                        "symbol",
                        "event_quote_bid_price",
                        "event_quote_ask_price",
                        "field__price__change__1__bar__pct",
                        "field__volume__rate__ratio",
                    ],
                    "column_intervals": {
                        "event_quote_bid_price": {"value": 100, "unit": "milliseconds"},
                        "event_quote_ask_price": {"value": 100, "unit": "milliseconds"},
                        "field__price__change__1__bar__pct": {"value": 5, "unit": "minutes"},
                        "field__volume__rate__ratio": {"value": 1, "unit": "seconds"},
                    },
                    "column_aggregations": {
                        "event_quote_bid_price": "last",
                        "event_quote_ask_price": "last",
                    },
                }
            ],
        }

        candidates = service.qmd_signal_stream_candidates(
            discovery,
            [
                {
                    "ticker": "AAA",
                    "symbol": "AAA",
                    "data.quote.bid_price@1:value@@100ms##last": 12.34,
                    "data.quote.ask_price@1:value@@100ms##last": 12.36,
                    "data.price_change_1_bar_pct@1:value@@5m": 6.25,
                    "data.volume_rate_ratio@1:value@@1s": 2.5,
                    "unrelated": "do not publish",
                }
            ],
        )

        self.assertEqual(candidates[0]["event_quote_bid_price"], 12.34)
        self.assertEqual(candidates[0]["event_quote_ask_price"], 12.36)
        self.assertEqual(candidates[0]["field__price__change__1__bar__pct"], 6.25)
        self.assertEqual(candidates[0]["field__volume__rate__ratio"], 2.5)
        self.assertNotIn("unrelated", candidates[0])

    def test_full_population_is_cached_before_per_request_slicing(self) -> None:
        cache = BoundedSingleFlightTtlCache[str, dict](
            max_entries=1,
            ttl_seconds=60,
            contract_revision="scanner-composition.test",
        )
        complete = {
            "provider": "qmd-gateway",
            "source_revision": "qmd-42",
            "schema_version": 2,
            "core_population_count": 3,
            "rows": [
                {"ticker": "AAPL"},
                {"ticker": "MSFT"},
                {"ticker": "NVDA"},
            ],
        }
        with (
            patch.object(service, "SCANNER_COMPOSITION_CACHE", cache),
            patch.object(
                service,
                "_compose_real_live_scanner_snapshot",
                return_value=complete,
            ) as compose,
        ):
            first = service.real_live_scanner_snapshot(row_limit=1)
            second = service.real_live_scanner_snapshot(row_limit=2)

        compose.assert_called_once_with(allow_provider_fallback=False)
        self.assertEqual(first["row_count"], 1)
        self.assertEqual(second["row_count"], 2)
        self.assertEqual(second["core_population_count"], 3)
        self.assertEqual(second["source_revision"], "qmd-42")
        self.assertEqual(second["feature_projection"]["row_count"], 2)

    def test_cold_presentation_request_returns_building_state_without_waiting(self) -> None:
        cache = BoundedSingleFlightTtlCache[str, dict](
            max_entries=2,
            ttl_seconds=60,
            contract_revision="scanner-composition.test",
        )
        with (
            patch.object(service, "SCANNER_COMPOSITION_CACHE", cache),
            patch.object(service, "SCANNER_LATEST_COMPLETE", None),
            patch.object(service, "SCANNER_REFRESH_ERROR", ""),
            patch.object(service, "SCANNER_REFRESH_GENERATION", None),
            patch.object(service, "SCANNER_CONFIGURATION_GENERATION", 17),
            patch.object(service.threading, "Thread") as thread,
        ):
            payload = service.real_live_scanner_snapshot(row_limit=250)

        self.assertEqual(payload["composition_status"], "building")
        self.assertEqual(payload["rows"], [])
        thread.return_value.start.assert_called_once_with()

    def test_expired_presentation_request_serves_last_complete_snapshot_while_refreshing(self) -> None:
        cache = BoundedSingleFlightTtlCache[str, dict](
            max_entries=2,
            ttl_seconds=60,
            contract_revision="scanner-composition.test",
        )
        previous = {"provider": "qmd-gateway", "rows": [{"ticker": "AAPL"}, {"ticker": "MSFT"}]}
        with (
            patch.object(service, "SCANNER_COMPOSITION_CACHE", cache),
            patch.object(service, "SCANNER_LATEST_COMPLETE", previous),
            patch.object(service, "SCANNER_REFRESH_ERROR", ""),
            patch.object(service, "SCANNER_REFRESH_GENERATION", None),
            patch.object(service, "SCANNER_CONFIGURATION_GENERATION", 19),
            patch.object(service.threading, "Thread") as thread,
        ):
            payload = service.real_live_scanner_snapshot(row_limit=1)

        self.assertEqual(payload["composition_status"], "refreshing")
        self.assertEqual(payload["rows"], [{"ticker": "AAPL"}])
        thread.return_value.start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
