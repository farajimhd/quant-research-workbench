from __future__ import annotations

import unittest
from unittest.mock import patch

from src.backend.qmd_gateway_client import qmd_bar_history, qmd_websocket_url


class QmdGatewayClientTests(unittest.TestCase):
    @patch("src.backend.qmd_gateway_client.qmd_get_json")
    def test_bar_history_uses_persisted_qmd_contract(self, get_json) -> None:
        get_json.return_value = {"history": [{"bar_start": "2026-07-10T08:00:00Z"}]}

        payload = qmd_bar_history("aapl", timeframe="5m", before="2026-07-14", days=1, row_limit=20000)

        self.assertEqual(len(payload["history"]), 1)
        get_json.assert_called_once_with(
            "/history/bars/AAPL",
            {"before": "2026-07-14", "days": 1, "timeframe": "5m", "limit": 20000},
            timeout=15,
        )

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
