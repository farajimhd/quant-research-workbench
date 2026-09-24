from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from src.trading_runtime.ibkr_client import _PersistentJsonTransport, _execution
from src.trading_runtime.ibkr_normalizer import normalize_execution


class PersistentJsonTransportTests(unittest.TestCase):
    def test_execution_commission_preserves_pending_vs_zero(self) -> None:
        row = {
            "execution_id": "e1", "symbol": "TEST", "side": "B",
            "trade_time_r": 1787040300000, "size": 1, "price": 10,
            "order_id": "o1", "account": "DU1", "conid": 1,
        }
        pending = _execution(row)
        self.assertIsNone(pending.commission)
        self.assertNotIn("commission", pending.to_cpapi())
        self.assertEqual(normalize_execution(pending.to_cpapi()).commission_status, "pending")
        finalized = _execution({**row, "commission": 0})
        self.assertEqual(finalized.commission, 0.0)
        self.assertEqual(normalize_execution(finalized.to_cpapi()).commission_status, "final")
        with self.assertRaisesRegex(ValueError, "not numeric"):
            _execution({**row, "commission": "unavailable"})

    def test_request_identifies_client_to_gateway(self) -> None:
        response = Mock(status=200, will_close=True)
        response.read.return_value = b'{"authenticated":true}'
        connection = Mock()
        connection.getresponse.return_value = response
        transport = _PersistentJsonTransport(
            "https://localhost:5000/v1/api",
            timeout=1,
            verify_tls=False,
        )

        with patch.object(transport, "_acquire", return_value=connection):
            status, body = transport.request("GET", "/iserver/auth/status", None)

        self.assertEqual(status, 200)
        self.assertEqual(body, '{"authenticated":true}')
        headers = connection.request.call_args.kwargs["headers"]
        self.assertEqual(headers["User-Agent"], "quant-research-workbench/1.0")


if __name__ == "__main__":
    unittest.main()
