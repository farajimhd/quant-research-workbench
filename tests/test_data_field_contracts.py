from __future__ import annotations

import unittest

from src.backend.data_field_contracts import (
    compile_data_field_plan,
    project_data_field_outputs,
)
from src.backend.trading_configuration_service import _default_draft


class DataFieldContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.discovery = _default_draft()["market_discovery"]

    def test_atomic_catalog_includes_market_clock_and_status(self) -> None:
        atomic_ids = {
            row["atomic_field_id"] for row in self.discovery["atomic_fields"]
        }
        self.assertTrue(
            {
                "clock.observed_at",
                "clock.trading_date",
                "clock.exchange_time",
                "clock.session_phase",
                "market.status",
                "market.is_open",
            }.issubset(atomic_ids)
        )

    def test_every_rule_condition_uses_an_exact_data_field_output(self) -> None:
        output_refs = {
            output["field_ref"]
            for data_field in self.discovery["data_fields"]
            for output in data_field["outputs"]
        }
        for rule_set in self.discovery["rule_sets"]:
            self.assertNotIn("atomic", rule_set)
            for condition in rule_set["conditions"]:
                self.assertIn(condition["left_field_ref"], output_refs)
                self.assertNotIn("left_timeframe", condition)
                self.assertNotIn("right_timeframe", condition)
                if condition.get("right_source_id"):
                    self.assertIn(condition["right_field_ref"], output_refs)

    def test_contextual_outputs_have_distinct_identities(self) -> None:
        refs = {
            data_field["context"]["interval"]: data_field["outputs"][0]["field_ref"]
            for data_field in self.discovery["data_fields"]
            if data_field["outputs"][0]["source_id"] == "rsi_14"
            and data_field["context"].get("interval")
        }
        self.assertNotEqual(refs["10s"], refs["5m"])

    def test_dimensions_follow_field_semantics(self) -> None:
        by_source: dict[str, list[dict]] = {}
        for data_field in self.discovery["data_fields"]:
            by_source.setdefault(data_field["outputs"][0]["source_id"], []).append(data_field)

        self.assertEqual(len(by_source["market.last_price"]), 1)
        self.assertEqual(
            by_source["market.last_price"][0]["context"],
            {
                "dimension_kind": "as_of",
                "as_of": "evaluation_clock",
                "available_intervals": [],
                "update_cadence": "service_owned",
                "execution_scope": "core_scan",
                "allowed_scopes": ["core_scan", "watchlist", "strategy_run", "request", "offline"],
            },
        )
        self.assertEqual(
            by_source["market.volume"][0]["context"]["anchor"],
            "market_session",
        )
        self.assertEqual(
            by_source["market.trade_rate_10s"][0]["context"]["window"],
            "10s",
        )
        price_change = by_source["price_change_pct"]
        self.assertTrue({"1m", "5m"}.issubset({row["context"]["interval"] for row in price_change}))
        self.assertTrue(all(row["outputs"][0]["name"] == "Price change" for row in price_change))
        self.assertTrue(all(row["context"]["dimension_kind"] == "interval" for row in price_change))

    def test_projection_populates_rule_and_canvas_identities(self) -> None:
        data_field = next(
            row
            for row in self.discovery["data_fields"]
            if any(output["source_id"] == "market.last_price" for output in row["outputs"])
        )
        output = data_field["outputs"][0]
        projected = project_data_field_outputs(
            [{"last_price": 101.25}], [data_field]
        )[0]
        self.assertEqual(projected[output["field_ref"]], 101.25)
        self.assertEqual(
            projected[output["column_presentations"][0]["presentation_id"]],
            101.25,
        )

    def test_compiler_derives_signal_stream_dependencies(self) -> None:
        discovery = {**self.discovery, "signal_streams": [{
            "signal_stream_id": "test-stream",
            "inclusion_rule_sets": ["initial-entry-confirmation-macd-confirmation"],
            "columns": [],
        }]}
        plan = compile_data_field_plan(discovery, composition_ids=["test-stream"])
        self.assertIn("5s", plan["technical_timeframes"])
        self.assertIn("initial-entry-confirmation-macd-confirmation", plan["rule_set_ids"])

    def test_price_volume_expansion_uses_session_semantics(self) -> None:
        rule_set = next(
            row
            for row in self.discovery["rule_sets"]
            if row["rule_set_id"] == "watchlist-price-or-volume-squeeze"
        )
        self.assertEqual(rule_set["name"], "Session Price or Volume Expansion")
        by_ref = {
            output["field_ref"]: data_field
            for data_field in self.discovery["data_fields"]
            for output in data_field["outputs"]
        }
        self.assertTrue(all(
            by_ref[condition["left_field_ref"]]["context"]["anchor"] == "market_session"
            for condition in rule_set["conditions"]
        ))


if __name__ == "__main__":
    unittest.main()
