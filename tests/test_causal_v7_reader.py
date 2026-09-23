from __future__ import annotations

from datetime import datetime
import json
import unittest
from zoneinfo import ZoneInfo

from src.backend.causal_v7_reader import CausalV7Cursor, CausalV7Unit, _hash


NY = ZoneInfo("America/New_York")


class _Client:
    def __init__(self, states: list[dict], levels: list[dict]) -> None:
        self.states = states
        self.levels = levels
        self.queries: list[str] = []

    def execute(self, sql: str) -> str:
        self.queries.append(sql)
        rows = self.states if "causal_v7_state_1s_v1" in sql else self.levels
        return "\n".join(json.dumps(row) for row in rows)


def fixture() -> tuple[CausalV7Unit, _Client]:
    metadata = [
        {"max_input_timestamp": datetime(2026, 8, 18, 4, 0, tzinfo=NY).timestamp(),
         "bars_processed": 0, "source_audit": {
             "source_hash": "a" * 64, "source_policy": "arte-priced-1s-bars-0405-et-v1",
             "consumed_through": datetime(2026, 8, 18, 4, 0, tzinfo=NY).timestamp()}},
        {"max_input_timestamp": datetime(2026, 8, 18, 4, 5, 1, tzinfo=NY).timestamp(),
         "bars_processed": 1, "source_audit": {
             "source_hash": "a" * 64, "source_policy": "arte-priced-1s-bars-0405-et-v1",
             "consumed_through": datetime(2026, 8, 18, 4, 5, 1, tzinfo=NY).timestamp()}},
    ]
    views = [[{"id": "prior"}], [{"id": "prior"}, {"id": "new"}]]
    states = [
        {"second_index": index, "level_revision": revision,
         "metadata_json": json.dumps(value), "metadata_hash": _hash(value)}
        for index, revision, value in zip((14_400, 14_701), (0, 1), metadata)
    ]
    levels = [
        {"second_index": index, "level_revision": revision,
         "levels_json": json.dumps(value), "levels_hash": _hash(value)}
        for index, revision, value in zip((14_400, 14_701), (0, 1), views)
    ]
    unit = CausalV7Unit("build", "2026-08-18", "TEST",
                        "00000000-0000-0000-0000-000000000001",
                        "bars", "a" * 64, "b" * 64, "seed", "catalog",
                        "c" * 64, 2, "state", 2, "levels")
    return unit, _Client(states, levels)


class CausalV7ReaderTests(unittest.TestCase):
    def test_completed_bar_becomes_visible_only_at_its_cutoff(self) -> None:
        unit, client = fixture()
        cursor = CausalV7Cursor(unit, client)
        before = cursor.snapshot(datetime(2026, 8, 18, 4, 5, 0, 900000, tzinfo=NY))
        after = cursor.snapshot(datetime(2026, 8, 18, 4, 5, 1, tzinfo=NY))
        self.assertEqual(before["bars_processed"], 0)
        self.assertEqual(after["bars_processed"], 1)
        self.assertEqual(len(after["unified_levels"]), 2)
        self.assertEqual(len(client.queries), 2)
        self.assertTrue(all(query.startswith("SELECT ") for query in client.queries))

    def test_future_input_and_mutated_hash_fail_closed(self) -> None:
        unit, client = fixture()
        client.states[0]["metadata_hash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "metadata changed"):
            CausalV7Cursor(unit, client)
        unit, client = fixture()
        client.states[1]["metadata_json"] = json.dumps({"max_input_timestamp":
            datetime(2026, 8, 18, 4, 5, 2, tzinfo=NY).timestamp(),
            "source_audit": {"source_hash": "a" * 64,
                             "source_policy": "arte-priced-1s-bars-0405-et-v1",
                             "consumed_through": datetime(2026, 8, 18, 4, 5, 1, tzinfo=NY).timestamp()}})
        client.states[1]["metadata_hash"] = _hash(json.loads(client.states[1]["metadata_json"]))
        with self.assertRaisesRegex(ValueError, "future input"):
            CausalV7Cursor(unit, client).snapshot(datetime(2026, 8, 18, 4, 5, 1, tzinfo=NY))


if __name__ == "__main__":
    unittest.main()
