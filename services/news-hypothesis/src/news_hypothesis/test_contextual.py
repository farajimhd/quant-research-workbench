from __future__ import annotations

import unittest

from .contextual import _summarize_price_action


class PriceActionContextTests(unittest.TestCase):
    def test_price_action_is_bounded_and_session_summarized(self) -> None:
        rows = []
        for day in range(1, 9):
            rows.extend([
                {"bar_start": f"2026-08-{day:02d}T13:30:00Z", "open": day, "high": day + 2, "low": day - 1, "close": day + 1, "volume": 10},
                {"bar_start": f"2026-08-{day:02d}T13:31:00Z", "open": day + 1, "high": day + 3, "low": day, "close": day + 2, "volume": 20},
            ])
        result = _summarize_price_action({"bars": rows})
        self.assertEqual(len(result), 6)
        self.assertEqual(result[-1]["last"], 10.0)
        self.assertEqual(result[-1]["volume"], 30.0)
        self.assertNotIn("bars", result[-1])


if __name__ == "__main__":
    unittest.main()
