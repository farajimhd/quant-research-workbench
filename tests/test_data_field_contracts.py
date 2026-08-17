from __future__ import annotations

import unittest
from copy import deepcopy

from src.backend.data_field_contracts import (
    _enrich_field_metadata,
    compile_data_field_plan,
    field_instance_ref,
    interval_expression,
    normalize_interval_spec,
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

    def test_calendar_components_are_computed_data_fields_not_atomic_fields(self) -> None:
        atomic_ids = {
            row["atomic_field_id"] for row in self.discovery["atomic_fields"]
        }
        by_source = {
            output["source_id"]: data_field
            for data_field in self.discovery["data_fields"]
            for output in data_field["outputs"]
        }
        expected = {
            "clock.calendar_year",
            "clock.calendar_quarter",
            "clock.month_number",
            "clock.month_name",
            "clock.iso_week",
            "clock.day_of_month",
            "clock.day_of_year",
            "clock.weekday_number",
            "clock.hour",
            "clock.minute",
            "clock.second",
            "clock.minutes_since_midnight",
            "clock.is_weekend",
            "clock.is_month_start",
            "clock.is_month_end",
            "clock.is_quarter_start",
            "clock.is_quarter_end",
        }
        self.assertTrue(expected.issubset(by_source))
        self.assertFalse(expected.intersection(atomic_ids))
        self.assertEqual(
            by_source["clock.weekday_number"]["inputs"],
            ["clock.exchange_date"],
        )
        self.assertEqual(
            by_source["clock.weekday_number"]["outputs"][0]["runtime_field"],
            "weekday_number",
        )

    def test_catalog_fields_publish_source_calculation_and_output_contracts(self) -> None:
        for data_field in self.discovery["data_fields"]:
            self.assertTrue(data_field["source"]["location"], data_field["data_field_id"])
            self.assertTrue(data_field["source"]["query_plan_id"], data_field["data_field_id"])
            self.assertTrue(data_field["calculation"]["summary"], data_field["data_field_id"])
            for output in data_field["outputs"]:
                self.assertTrue(output["runtime_field"], output["field_ref"])
        source_ids = {
            output["source_id"]
            for data_field in self.discovery["data_fields"]
            for output in data_field["outputs"]
        }
        self.assertFalse(any(value.startswith("qmd.primitive.") for value in source_ids))
        self.assertFalse(any(value.startswith("qmd.family.") for value in source_ids))

    def test_categorical_fields_publish_known_values(self) -> None:
        by_source = {
            output["source_id"]: data_field
            for data_field in self.discovery["data_fields"]
            for output in data_field["outputs"]
        }
        self.assertEqual(
            [row["value"] for row in by_source["clock.weekday"]["known_values"]],
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        )
        self.assertEqual(len(by_source["clock.month_name"]["known_values"]), 12)
        self.assertEqual(
            {row["value"] for row in by_source["clock.session_phase"]["known_values"]},
            {"premarket", "regular", "aftermarket", "maintenance"},
        )
        self.assertEqual(
            {row["value"] for row in by_source["classification.market_cap"]["known_values"]},
            {"Small Cap", "Mid Cap", "Large Cap"},
        )
        self.assertEqual(
            by_source["clock.session_phase"]["outputs"][0]["value_domain"]["kind"],
            "enum",
        )
        self.assertTrue(by_source["clock.session_phase"]["outputs"][0]["value_domain"]["closed"])
        self.assertEqual(
            by_source["market.last_price"]["outputs"][0]["value_domain"]["kind"],
            "number",
        )
        self.assertIn(
            "does not roll weekends or holidays",
            by_source["clock.trading_date"]["calculation"]["summary"],
        )

    def test_catalog_only_fields_are_not_offered_as_rule_inputs(self) -> None:
        industry = next(
            row for row in self.discovery["data_fields"]
            if row["outputs"][0]["source_id"] == "classification.industry"
        )
        self.assertFalse(industry["enabled"])
        self.assertFalse(industry["execution"]["market_discovery_supported"])
        weekday = next(
            row for row in self.discovery["data_fields"]
            if row["outputs"][0]["source_id"] == "clock.weekday"
        )
        self.assertTrue(weekday["enabled"])
        self.assertTrue(weekday["execution"]["market_discovery_supported"])

    def test_structured_interval_compiles_without_becoming_field_identity(self) -> None:
        interval = {"value": 3, "unit": "minutes"}
        self.assertEqual(normalize_interval_spec("3m"), interval)
        self.assertEqual(interval_expression(interval), "3m")
        self.assertEqual(field_instance_ref("data.test@1:value", interval), "data.test@1:value@@3m")
        subsecond = {"value": 25, "unit": "milliseconds"}
        self.assertEqual(normalize_interval_spec("25ms"), subsecond)
        self.assertEqual(interval_expression(subsecond), "25ms")
        self.assertEqual(field_instance_ref("data.test@1:value", subsecond), "data.test@1:value@@25ms")

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

    def test_rule_operands_bind_intervals_only_when_the_data_field_requires_one(self) -> None:
        by_ref = {
            output["field_ref"]: data_field
            for data_field in self.discovery["data_fields"]
            for output in data_field["outputs"]
        }
        interval_operands = 0
        for rule_set in self.discovery["rule_sets"]:
            for condition in rule_set["conditions"]:
                for side in ("left", "right"):
                    field_ref = condition.get(f"{side}_field_ref")
                    if not field_ref:
                        continue
                    self.assertNotIn(f"{side}_value_selection", condition)
                    data_field = by_ref[field_ref]
                    interval = normalize_interval_spec(condition.get(f"{side}_interval"))
                    if data_field["context"]["dimension_kind"] == "interval":
                        interval_operands += 1
                        self.assertIsNotNone(interval, (rule_set["rule_set_id"], side, field_ref))
                    else:
                        self.assertIsNone(interval, (rule_set["rule_set_id"], side, field_ref))
        self.assertGreater(interval_operands, 0)

        squeeze = next(
            row for row in self.discovery["rule_sets"]
            if row["rule_set_id"] == "watchlist-price-or-volume-squeeze"
        )
        self.assertEqual(
            [by_ref[row["left_field_ref"]]["context"]["anchor"] for row in squeeze["conditions"]],
            ["market_session", "market_session"],
        )

    def test_event_fields_require_typed_window_aggregations(self) -> None:
        by_source = {
            output["source_id"]: (data_field, output)
            for data_field in self.discovery["data_fields"]
            for output in data_field["outputs"]
        }
        trade_price, output = by_source["trade.price"]
        self.assertTrue(trade_price["enabled"])
        self.assertEqual(trade_price["context"]["interval_semantics"], "event_window")
        self.assertEqual(trade_price["context"]["aggregation"]["default"], "last")
        self.assertEqual(
            trade_price["execution"]["aggregation_runtime_fields"]["max"],
            "high",
        )
        instance = field_instance_ref(
            output["field_ref"], {"value": 3, "unit": "minutes"}, "max"
        )
        self.assertTrue(instance.endswith("@@3m##max"))
        projected = project_data_field_outputs(
            [{"working_timeframe": "1m", "high": 12.5}],
            [trade_price],
        )
        self.assertEqual(
            projected[0][field_instance_ref(output["field_ref"], "1m", "max")],
            12.5,
        )

    def test_decoded_quote_and_trade_members_are_atomic_catalog_entries(self) -> None:
        atomic_ids = {row["atomic_field_id"] for row in self.discovery["atomic_fields"]}
        self.assertTrue({"trade.price", "trade.size", "trade.conditions", "quote.bid_price", "quote.ask_size", "quote.indicators"}.issubset(atomic_ids))

    def test_enabled_rules_use_only_executable_data_fields(self) -> None:
        by_ref = {
            output["field_ref"]: data_field
            for data_field in self.discovery["data_fields"]
            for output in data_field["outputs"]
        }
        for rule_set in self.discovery["rule_sets"]:
            if not rule_set["enabled"]:
                continue
            for condition in rule_set["conditions"]:
                self.assertTrue(by_ref[condition["left_field_ref"]]["enabled"])
                if condition.get("right_field_ref"):
                    self.assertTrue(by_ref[condition["right_field_ref"]]["enabled"])

        pending_ids = {
            "watchlist-news-bullish", "watchlist-news-bearish",
            "watchlist-sec-bullish", "watchlist-sec-bearish",
        }
        pending = {
            row["rule_set_id"]: row for row in self.discovery["rule_sets"]
            if row["rule_set_id"] in pending_ids
        }
        self.assertEqual(set(pending), pending_ids)
        self.assertTrue(all(not row["enabled"] for row in pending.values()))
        self.assertTrue(all(row["implementation_status"] == "integration_pending" for row in pending.values()))

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
                "allowed_scopes": ["core_scan", "watchlist", "signal_stream", "strategy_run", "request", "offline"],
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
        self.assertTrue(all(row["outputs"][0]["name"] == "Bar price change %" for row in price_change))
        self.assertTrue(all(row["context"]["dimension_kind"] == "interval" for row in price_change))

    def test_change_outputs_have_typed_units_and_explicit_baselines(self) -> None:
        expected = {
            "price_change_1_bar": ("currency", "current_close - comparison_close"),
            "return_1_bar": ("percent", "abs(comparison_close)"),
            "volume_change": ("shares", "current - previous_bar"),
            "volume_change_pct": ("percent", "abs(previous_bar)"),
            "volume_ratio": ("multiple", "current / previous_bar"),
            "dollar_volume_change": ("currency", "current - previous_bar"),
            "trade_count_change_pct": ("percent", "abs(previous_bar)"),
            "quote_count_ratio": ("multiple", "current / previous_bar"),
        }
        for source_id, (unit, formula_fragment) in expected.items():
            field = _enrich_field_metadata(source_id, {})
            self.assertEqual(field["unit"], unit, source_id)
            self.assertIn(formula_fragment, field["formula"], source_id)

        by_source = {
            output["source_id"]: (data_field, output)
            for data_field in self.discovery["data_fields"]
            for output in data_field["outputs"]
        }
        for source_id, unit in {
            "market.change_actual": "currency",
            "fundamental.revenue_change": "currency",
            "fundamental.revenue_growth_pct": "percent",
            "fundamental.earnings_change": "currency",
            "fundamental.earnings_growth_pct": "percent",
            "fundamental.share_change": "shares",
            "fundamental.share_growth_pct": "percent",
        }.items():
            data_field, output = by_source[source_id]
            self.assertTrue(data_field["enabled"], source_id)
            self.assertEqual(output["unit"], unit, source_id)

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

    def test_projection_can_be_limited_to_active_field_references(self) -> None:
        last_price = next(
            output
            for data_field in self.discovery["data_fields"]
            for output in data_field["outputs"]
            if output["source_id"] == "market.last_price"
        )
        volume = next(
            output
            for data_field in self.discovery["data_fields"]
            for output in data_field["outputs"]
            if output["source_id"] == "market.volume"
        )
        projected = project_data_field_outputs(
            [{"last_price": 101.25, "volume": 50_000}],
            self.discovery["data_fields"],
            field_refs=[last_price["field_ref"]],
        )[0]
        self.assertEqual(projected[last_price["field_ref"]], 101.25)
        self.assertNotIn(volume["field_ref"], projected)

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

    def test_squeeze_detection_rules_use_explicit_fast_change_instances(self) -> None:
        squeeze_rules = {
            row["rule_set_id"]: row
            for row in self.discovery["rule_sets"]
            if row["rule_set_id"].startswith("watchlist-squeeze-")
        }
        expected = {
            "watchlist-squeeze-early-impulse-100ms": (
                "100ms",
                {"price_change_1_bar_pct", "trade_count_change", "volume_change"},
            ),
            "watchlist-squeeze-acceleration-1s": (
                "1s",
                {"price_change_1_bar_pct", "trade_rate_ratio", "volume_rate_ratio"},
            ),
            "watchlist-squeeze-confirmation-10s": (
                "10s",
                {"price_change_1_bar_pct", "trade_count_ratio", "volume_ratio"},
            ),
            "watchlist-squeeze-buy-pressure-1s": (
                "1s",
                {"price_change_1_bar_pct", "buy_sell_volume_delta"},
            ),
        }
        self.assertEqual(set(squeeze_rules), set(expected))
        for rule_set_id, (interval, sources) in expected.items():
            rule_set = squeeze_rules[rule_set_id]
            self.assertEqual({row["left_source_id"] for row in rule_set["conditions"]}, sources)
            self.assertEqual(
                {interval_expression(row["left_interval"]) for row in rule_set["conditions"]},
                {interval},
            )

        inactive = compile_data_field_plan({
            **self.discovery,
            "core_scan": {**self.discovery["core_scan"], "inclusion_rule_sets": []},
            "watchlists": [],
            "signal_streams": [],
        })
        self.assertTrue(set(expected).isdisjoint(inactive["rule_set_ids"]))

        materialized = deepcopy(self.discovery)
        materialized["core_scan"]["inclusion_rule_sets"] = [
            "watchlist-squeeze-early-impulse-100ms"
        ]
        plan = compile_data_field_plan(materialized)
        self.assertIn("watchlist-squeeze-early-impulse-100ms", plan["rule_set_ids"])
        self.assertIn("100ms", plan["technical_timeframes"])

    def test_materialized_event_window_keeps_aggregation_in_compiled_identity(self) -> None:
        discovery = deepcopy(self.discovery)
        trade_price = next(
            row for row in discovery["data_fields"]
            if row["outputs"][0]["source_id"] == "trade.price"
        )
        output = trade_price["outputs"][0]
        discovery["rule_sets"].append({
            "rule_set_id": "event-window-test",
            "name": "Event window test",
            "description": "Test typed event aggregation.",
            "enabled": True,
            "operator": "all",
            "required_score": 1,
            "conditions": [{
                "condition_id": "event-window-test-1",
                "enabled": True,
                "left_source_id": "trade.price",
                "left_field_ref": output["field_ref"],
                "left_interval": {"value": 3, "unit": "minutes"},
                "left_aggregation": "max",
                "comparator": "greater_than",
                "right_source_id": "",
                "value": 10,
            }],
        })
        discovery["core_scan"]["inclusion_rule_sets"] = ["event-window-test"]
        plan = compile_data_field_plan(discovery)
        instance = next(row for row in plan["field_instances"] if row["field_ref"] == output["field_ref"])
        self.assertEqual(instance["aggregation"], "max")
        self.assertEqual(instance["instance_ref"], field_instance_ref(output["field_ref"], "3m", "max"))


if __name__ == "__main__":
    unittest.main()
