from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from src.trading_runtime.ibkr_client import _PersistentJsonTransport


class PersistentJsonTransportTests(unittest.TestCase):
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
