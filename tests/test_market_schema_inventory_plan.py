from __future__ import annotations

import unittest
from unittest.mock import Mock

from src.backend.application_registry import QUERY_PLANS
from src.backend.query_plans.market_schema_inventory_v1 import (
    QUERY_PLAN_ID,
    column_inventory,
    configured_table_columns,
    configured_table_count_buckets,
    configured_table_preview,
    configured_table_stats,
    table_inventory,
)
from src.backend.real_live_market_data.gateway import (
    query_universe_preview_columns,
    query_universe_preview_tables,
)


class MarketSchemaInventoryPlanTests(unittest.TestCase):
    def test_plan_is_registered_and_database_bounded(self) -> None:
        plan = next(row for row in QUERY_PLANS if row.plan_id == QUERY_PLAN_ID)

        self.assertEqual(plan.source_paths, ("system.tables", "system.columns"))
        self.assertIn("database = currentDatabase()", table_inventory())
        self.assertIn("database = currentDatabase()", column_inventory())

    def test_gateway_executes_registered_builders(self) -> None:
        client = Mock()
        client.query_json.side_effect = [[{"name": "events"}], [{"name": "ticker"}]]

        self.assertEqual(query_universe_preview_tables(client), [{"name": "events"}])
        self.assertEqual(query_universe_preview_columns(client), [{"name": "ticker"}])
        self.assertEqual(client.query_json.call_args_list[0].args[0], table_inventory())
        self.assertEqual(client.query_json.call_args_list[1].args[0], column_inventory())
        self.assertEqual(client.query_json.call_args_list[0].kwargs["timeout"], 8)

    def test_operational_queries_are_bounded_to_authorized_targets(self) -> None:
        targets = [{"database": "q_live", "table": "events"}]
        stats = configured_table_stats(targets)
        columns = configured_table_columns(targets)
        preview = configured_table_preview(
            database="q_live",
            table="events",
            time_column="published_at_utc",
            limit=999,
        )
        buckets = configured_table_count_buckets(
            targets,
            {("q_live", "events"): {"published_at_utc"}},
            years=(2026, 2025),
        )

        self.assertIn("('q_live', 'events')", stats)
        self.assertIn("('q_live', 'events')", columns)
        self.assertIn("FROM `q_live`.`events`", preview)
        self.assertIn("ORDER BY `published_at_utc` DESC", preview)
        self.assertIn("LIMIT 100", preview)
        self.assertIn("rows_2026", buckets)
        self.assertIn("FROM `q_live`.`events`", buckets)


if __name__ == "__main__":
    unittest.main()
