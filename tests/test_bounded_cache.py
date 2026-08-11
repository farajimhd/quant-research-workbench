from __future__ import annotations

import unittest
from unittest.mock import patch

from src.backend.bounded_cache import BoundedTtlCache


class BoundedTtlCacheTests(unittest.TestCase):
    def test_expires_and_rejects_a_changed_source_revision(self) -> None:
        now = [10.0]
        cache = BoundedTtlCache[str, str](
            max_entries=2,
            ttl_seconds=5,
            contract_revision="example.v1",
            clock=lambda: now[0],
        )
        cache.set("A", "first", source_revision="source-1")

        self.assertEqual(cache.get("A", source_revision="source-1"), "first")
        self.assertIsNone(cache.get("A", source_revision="source-2"))
        cache.set("A", "second", source_revision="source-2")
        now[0] = 15.0
        self.assertIsNone(cache.get("A", source_revision="source-2"))

    def test_evicts_the_least_recently_used_entry_at_the_bound(self) -> None:
        cache = BoundedTtlCache[str, int](
            max_entries=2,
            ttl_seconds=60,
            contract_revision="example.v1",
        )
        cache.set("A", 1)
        cache.set("B", 2)
        self.assertEqual(cache.get("A"), 1)
        cache.set("C", 3)

        self.assertIsNone(cache.get("B"))
        self.assertEqual(cache.get("A"), 1)
        self.assertEqual(cache.get("C"), 3)
        self.assertEqual(cache.metrics()["evictions"], 1)

    def test_validates_cache_governance(self) -> None:
        with self.assertRaises(ValueError):
            BoundedTtlCache(max_entries=0, ttl_seconds=1, contract_revision="v1")
        with self.assertRaises(ValueError):
            BoundedTtlCache(max_entries=1, ttl_seconds=0, contract_revision="v1")
        with self.assertRaises(ValueError):
            BoundedTtlCache(max_entries=1, ttl_seconds=1, contract_revision="")

    def test_service_table_state_uses_the_bounded_cache(self) -> None:
        from src.backend import app as backend_app

        service_id = "bounded-cache-test"
        targets = [{"database": "q_live", "table": "events", "role": "events"}]
        with (
            patch.dict(
                backend_app.SERVICE_DATABASE_TABLES,
                {service_id: targets},
                clear=False,
            ),
            patch.object(
                backend_app,
                "clickhouse_table_stats",
                return_value={},
            ) as table_stats,
        ):
            first = backend_app.service_database_table_state(service_id)
            second = backend_app.service_database_table_state(service_id)

        self.assertEqual(first, second)
        table_stats.assert_called_once_with(targets)


if __name__ == "__main__":
    unittest.main()
