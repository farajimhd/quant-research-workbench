from __future__ import annotations

import unittest

from scripts.validate_application_read_load import (
    decode_chunked_body,
    percentile,
    validate_chart,
    validate_planner,
    validate_scanner,
    validate_watchlists,
)


class ApplicationReadLoadAcceptanceTests(unittest.TestCase):
    def test_chunked_body_decoder_preserves_json_bytes(self) -> None:
        self.assertEqual(
            decode_chunked_body(b"3\r\n{\"a\r\n3\r\n\":1\r\n1\r\n}\r\n0\r\n\r\n"),
            b'{"a":1}',
        )

    def test_contract_validators_accept_bounded_authoritative_shapes(self) -> None:
        validate_scanner({"rows": [{"ticker": "AAPL"}], "row_count": 1})
        validate_watchlists(
            {"watchlists": [], "computation_requirements": {"complete": True}}
        )
        validate_chart(
            {
                "source": "qmd-gateway",
                "bars": {"history": [], "current": None},
            }
        )
        validate_planner({"schema_version": 1, "authorities": {}})

    def test_contract_validators_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "row_count"):
            validate_scanner({"rows": [], "row_count": 1})
        with self.assertRaisesRegex(ValueError, "source authority"):
            validate_chart({"source": "fallback", "bars": {"history": []}})

    def test_percentile_uses_nearest_rank(self) -> None:
        self.assertEqual(percentile([10.0, 20.0, 30.0, 40.0], 0.50), 20.0)
        self.assertEqual(percentile([10.0, 20.0, 30.0, 40.0], 0.95), 40.0)
        self.assertIsNone(percentile([], 0.95))


if __name__ == "__main__":
    unittest.main()
