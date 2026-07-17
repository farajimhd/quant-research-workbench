from __future__ import annotations

import unittest

import polars as pl

from src.backend.news_service import news_heat_expr


class NewsTemperatureTests(unittest.TestCase):
    def test_news_temperature_boundaries_follow_product_contract(self) -> None:
        frame = pl.DataFrame({"age": [0, 240, 241, 1440, 1441]}).with_columns(
            news_heat_expr("age").alias("temperature")
        )
        self.assertEqual(frame["temperature"].to_list(), ["hot", "hot", "cold", "cold", "old"])


if __name__ == "__main__":
    unittest.main()
