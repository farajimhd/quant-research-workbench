from __future__ import annotations

import unittest

from pipelines.reference_data.clickhouse_load_market_references import (
    DEFAULT_REFERENCE_DIR,
    build_condition_token_rows,
    glossary_condition_rows,
    official_trade_update_rules,
)


class TradeAggregationReferenceTests(unittest.TestCase):
    def test_official_api_rules_override_blank_glossary_cells(self) -> None:
        rules = official_trade_update_rules(DEFAULT_REFERENCE_DIR)

        self.assertEqual(rules[2], (0, 0, 1))  # Average Price Trade
        self.assertEqual(rules[12], (0, 0, 1))  # Form T outside session is handled by the bar engine
        self.assertEqual(rules[41], (1, 1, 1))  # Trade Thru Exempt

    def test_trade_condition_table_and_tokens_share_official_rules(self) -> None:
        _, table_rows = glossary_condition_rows(
            DEFAULT_REFERENCE_DIR / "conditions_indicators_glossary.json",
            "trade_conditions",
        )
        table_rule = next(row for row in table_rows if row["modifier_int"] == 41)
        token_rule = next(
            row
            for row in build_condition_token_rows(DEFAULT_REFERENCE_DIR)
            if row["source_family"] == "trade_conditions" and row["modifier_int"] == 41
        )

        for row in (table_rule, token_rule):
            self.assertEqual(row["update_high_low"], 1)
            self.assertEqual(row["update_last"], 1)
            self.assertEqual(row["update_volume"], 1)

    def test_regular_sale_retains_glossary_rule_when_api_has_no_row_zero(self) -> None:
        _, rows = glossary_condition_rows(
            DEFAULT_REFERENCE_DIR / "conditions_indicators_glossary.json",
            "trade_conditions",
        )
        regular_sale = next(row for row in rows if row["modifier_int"] == 0)

        self.assertEqual(
            (
                regular_sale["update_high_low"],
                regular_sale["update_last"],
                regular_sale["update_volume"],
            ),
            (1, 1, 1),
        )


if __name__ == "__main__":
    unittest.main()
