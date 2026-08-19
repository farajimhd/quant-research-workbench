from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

from src.backend.application_registry import FIELD_BY_ID
from src.backend.bar_gpt_client import publish_bar_gpt_scope
from src.backend.model_feature_store import ModelFeatureStore
from src.backend.trading_configuration_service import _default_market_discovery, _validate_market_discovery


class BarGptServingTests(unittest.TestCase):
    def test_raw_and_decoded_heads_are_registered_for_rules(self) -> None:
        raw = "model.bargpt.v2.physical.1m.trade_close_return.q50.raw"
        decoded = "model.bargpt.v2.physical.1m.trade_close_return.q50.value"
        gap = "model.bargpt.v3.next_bar.1s.gap_logit.one_interval"
        self.assertLessEqual({raw, decoded, gap}, set(FIELD_BY_ID))
        discovery = _default_market_discovery([], [])
        catalog = {row["field_id"]: row for row in discovery["field_catalog"]}
        self.assertTrue(catalog[raw]["market_discovery_supported"])
        self.assertTrue(catalog[raw]["filterable"])

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
        with self.assertRaisesRegex(ValueError, "backward"):
            store.publish({**first, "prediction_id": "p0", "event_at_us": now_us - 1, "available_at_us": now_us - 1})

    def test_scope_client_backs_off_when_service_is_offline(self) -> None:
        with patch.dict(os.environ, {"BAR_GPT_SERVICE_URL": "http://127.0.0.1:1"}):
            result = publish_bar_gpt_scope("test", mode="live", tickers=["AAPL"], timeout=0.01)
        self.assertEqual(result["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
