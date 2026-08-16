from __future__ import annotations

import unittest

from src.backend.data_field_contracts import (
    compile_data_field_plan,
    field_instance_ref,
    project_composition_data_field_columns,
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

    def test_interval_is_not_part_of_the_read_only_data_field_identity(self) -> None:
        rsi = [
            data_field for data_field in self.discovery["data_fields"]
            if data_field["outputs"][0]["source_id"] == "rsi_14"
        ]
        self.assertEqual(len(rsi), 1)
        self.assertNotIn("interval", rsi[0]["context"])
        self.assertTrue({"10s", "5m"}.issubset(rsi[0]["context"]["available_intervals"]))
        self.assertEqual(rsi[0]["outputs"][0]["field_ref"], "data.rsi_14@1:value")

    def test_macro_intervals_are_exposed_only_for_supported_core_bar_outputs(self) -> None:
        by_source = {
            row["outputs"][0]["source_id"]: row
            for row in self.discovery["data_fields"]
        }
        self.assertTrue(
            {"1d", "1w", "1mo"}.issubset(
                by_source["close"]["context"]["available_intervals"]
            )
        )
        self.assertFalse(
            {"1d", "1w", "1mo"}.intersection(
                by_source["vwap"]["context"]["available_intervals"]
            )
        )

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
        self.assertEqual(len(price_change), 1)
        self.assertTrue({"1m", "5m"}.issubset(price_change[0]["context"]["available_intervals"]))
        self.assertTrue(all(row["outputs"][0]["name"] == "Price change" for row in price_change))
        self.assertTrue(all(row["context"]["dimension_kind"] == "interval" for row in price_change))

    def test_interval_instance_projects_to_a_stable_canvas_column(self) -> None:
        data_field = next(
            row for row in self.discovery["data_fields"]
            if row["outputs"][0]["source_id"] == "price_change_pct"
        )
        output = data_field["outputs"][0]
        column_id = output["column_presentations"][0]["presentation_id"]
        projected = project_data_field_outputs(
            [{"technical__price_change_pct__5m": 3.25}], [data_field]
        )
        self.assertEqual(projected[0][field_instance_ref(output["field_ref"], "5m")], 3.25)
        canvas = project_composition_data_field_columns(
            projected,
            {"columns": [column_id], "column_intervals": {column_id: "5m"}},
            [{"column_id": column_id, "field_ref": output["field_ref"]}],
        )
        self.assertEqual(canvas[0][column_id], 3.25)

        discovery = {
            **self.discovery,
            "core_scan": {
                **self.discovery["core_scan"],
                "columns": [column_id],
                "column_intervals": {column_id: "5m"},
                "inclusion_rule_sets": [],
            },
            "watchlists": [],
            "signal_streams": [],
        }
        self.assertIn("5m", compile_data_field_plan(discovery)["technical_timeframes"])

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

    def test_unused_rule_set_does_not_activate_qmd_until_market_discovery_selects_it(self) -> None:
        discovery = {**self.discovery, "core_scan": {**self.discovery["core_scan"], "inclusion_rule_sets": []}, "watchlists": [], "signal_streams": []}
        plan = compile_data_field_plan(discovery)
        self.assertNotIn("initial-entry-confirmation-macd-confirmation", plan["rule_set_ids"])
        discovery["watchlists"] = [{
            "watchlist_id": "active",
            "enabled": True,
            "availability": "available",
            "inclusion_rule_sets": ["initial-entry-confirmation-macd-confirmation"],
            "columns": [],
        }]
        active = compile_data_field_plan(discovery)
        self.assertIn("initial-entry-confirmation-macd-confirmation", active["rule_set_ids"])
        self.assertIn("5s", active["technical_timeframes"])
        discovery["watchlists"][0]["enabled"] = False
        disabled = compile_data_field_plan(discovery)
        self.assertNotIn("initial-entry-confirmation-macd-confirmation", disabled["rule_set_ids"])

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
