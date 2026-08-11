from __future__ import annotations

import unittest
from unittest.mock import Mock

from src.backend.application_registry import QUERY_PLANS
from src.backend.query_plans.market_schema_inventory_v1 import (
    QUERY_PLAN_ID,
    column_inventory,
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


if __name__ == "__main__":
    unittest.main()
