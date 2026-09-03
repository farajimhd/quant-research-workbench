from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

from src.backend.application_registry import FIELD_BY_ID
from src.backend.bar_gpt_client import _stable_unique, publish_bar_gpt_scope
from src.backend.model_feature_store import ModelFeatureStore
from src.backend.trading_configuration_service import _default_market_discovery, _validate_market_discovery
from src.backend.real_live_trading_service import _ranked_unique_tickers


class BarGptServingTests(unittest.TestCase):
    def test_ranked_watchlist_authority_survives_deduplication_and_cap(self) -> None:
        watchlists = [
            {"members": [{"ticker": "MSFT"}, {"ticker": "AAPL"}]},
            {"members": [{"ticker": "AAPL"}, {"ticker": "NVDA"}]},
        ]
        self.assertEqual(_ranked_unique_tickers(watchlists, 2), ["MSFT", "AAPL"])
        self.assertEqual(_stable_unique(["msft", "AAPL", "MSFT"], uppercase=True), ["MSFT", "AAPL"])
    def test_raw_and_decoded_heads_are_registered_for_rules(self) -> None:
        raw = "model.bargpt.v2.physical.1m.trade_close_return.q50.raw"
        decoded = "model.bargpt.v2.physical.1m.trade_close_return.q50.value"
        gap = "model.bargpt.v3.next_bar.1s.gap_logit.one_interval"
        self.assertLessEqual({raw, decoded, gap}, set(FIELD_BY_ID))
        discovery = _default_market_discovery([], [])
        catalog = {row["field_id"]: row for row in discovery["field_catalog"]}
        self.assertTrue(catalog[raw]["market_discovery_supported"])
        self.assertTrue(catalog[raw]["filterable"])
        self.assertEqual(
            FIELD_BY_ID[raw].label,
            "BarGPT V2 · Physical 1m · Trade Close Return · Median quantile (q50) · Raw head",
        )
        self.assertEqual(
            FIELD_BY_ID[decoded].label,
            "BarGPT V2 · Physical 1m · Trade Close Forecast Price · Median quantile (q50) · Decoded value",
        )
        self.assertNotIn(FIELD_BY_ID[raw].label, {"Raw", "Value", "Probability", "Logit", "Vector"})

        bar_gpt_labels = [
            field.label
            for field in FIELD_BY_ID.values()
            if field.field_id.startswith("model.bargpt.")
        ]
        self.assertEqual(len(bar_gpt_labels), 2_984)
        self.assertEqual(len(set(bar_gpt_labels)), len(bar_gpt_labels))
        self.assertFalse(
            {label.casefold() for label in bar_gpt_labels}
            & {"raw", "value", "probability", "logit", "logits", "vector"}
        )

    def test_model_serving_watchlist_contract_fails_closed(self) -> None:
        discovery = _default_market_discovery([], [])
        discovery["model_serving"]["bar_gpt"]["watchlist_ids"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "unknown Watchlists"):
            _validate_market_discovery(discovery)

    def test_feature_store_preserves_raw_fields_and_rejects_clock_regression(self) -> None:
        store = ModelFeatureStore()
        now_us = time.time_ns() // 1_000
        first = {
            "prediction_id": "p1", "ticker": "aapl", "model_id": "bar_gpt_v2",
            "model_version": "v2", "checkpoint_hash": "hash", "event_at_us": now_us,
            "mode": "live", "scope_id": "live:test",
            "available_at_us": now_us, "fields": {"model.bargpt.v2.raw": -0.25},
            "raw": {"head": [-0.25]},
        }
        self.assertEqual(store.publish(first)["status"], "accepted")
        self.assertEqual(store.project_rows([{"ticker": "AAPL"}])[0]["model.bargpt.v2.raw"], -0.25)
        self.assertEqual(
            store.scoped_fields(mode="live", scope_id="live:test", ticker="AAPL", as_of_us=now_us)["model.bargpt.v2.raw"],
            -0.25,
        )
        self.assertEqual(store.scoped_fields(mode="replay", scope_id="live:test", ticker="AAPL", as_of_us=now_us), {})
        with self.assertRaisesRegex(ValueError, "backward"):
            store.publish({**first, "prediction_id": "p0", "event_at_us": now_us - 1, "available_at_us": now_us - 1})

    def test_chart_forecasts_project_matching_next_bar_timeframe(self) -> None:
        store = ModelFeatureStore()
        origin_us = 1_800_000_000_000_000
        fields = {
            f"model.bargpt.v3.next_bar.{view}.trade_{component}_return.value": value
            for view, offset in (("1s", 0.0), ("5s", 1.0))
            for component, value in {
                "open": 100.0 + offset,
                "high": 102.0 + offset,
                "low": 99.0 + offset,
                "close": 101.0 + offset,
            }.items()
        }
        store.publish({
            "prediction_id": "next-bars", "ticker": "AAPL", "model_id": "bar_gpt_v3_epoch2",
            "model_version": "v3", "checkpoint_hash": "hash", "event_at_us": origin_us,
            "mode": "replay", "scope_id": "replay:origin", "available_at_us": origin_us,
            "fields": fields, "raw": {},
        })

        payload = store.chart_forecasts(
            "AAPL", model_version="v3", scope_id="replay:origin",
            forecast_kind="next_bar", timeframe="1s",
        )

        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(payload["rows"][0]["forecast_kind"], "next_bar")
        self.assertEqual(payload["rows"][0]["timeframe"], "1s")
        self.assertEqual(payload["rows"][0]["target_start_us"], origin_us)
        self.assertEqual(payload["rows"][0]["target_end_us"], origin_us + 1_000_000)
        self.assertEqual(payload["rows"][0]["close"], 101.0)

    def test_chart_forecasts_preserve_physical_default(self) -> None:
        store = ModelFeatureStore()
        origin_us = 1_800_000_000_000_000
        fields = {
            f"model.bargpt.v3.physical.5s.trade_{component}_return.q50.value": value
            for component, value in {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0}.items()
        }
        store.publish({
            "prediction_id": "physical", "ticker": "AAPL", "model_id": "bar_gpt_v3_epoch2",
            "model_version": "v3", "checkpoint_hash": "hash", "event_at_us": origin_us,
            "mode": "replay", "scope_id": "replay:origin", "available_at_us": origin_us,
            "fields": fields, "raw": {},
        })

        payload = store.chart_forecasts("AAPL", model_version="v3", scope_id="replay:origin")

        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(payload["rows"][0]["forecast_kind"], "physical")
        self.assertEqual(payload["rows"][0]["target_end_us"], origin_us + 5_000_000)

    def test_scope_client_backs_off_when_service_is_offline(self) -> None:
        with patch.dict(os.environ, {"BAR_GPT_SERVICE_URL": "http://127.0.0.1:1"}):
            result = publish_bar_gpt_scope("test", mode="live", tickers=["AAPL"], timeout=0.01)
        self.assertEqual(result["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
