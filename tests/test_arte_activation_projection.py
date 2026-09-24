from __future__ import annotations

import unittest
import json
import re
from datetime import date, datetime
from decimal import Decimal

from src.trading_runtime.arte_activation_projection import (
    ACTIVATION_TABLES, load_activation, prepare_activation_rows,
    project_activation, publish_activation, restore_activation,
)
from src.trading_runtime.strategy_activation import strategy_observation_from_signal_occurrence


def _delivery() -> dict:
    at = "2026-08-21T08:10:01+00:00"
    return {
        "delivery_id": "plan-1:event-1", "run_plan_id": "plan-1",
        "profile_id": "profile-1", "book_id": "default", "ticker": "SUGP",
        "signal_stream_id": "squeeze", "event_id": "event-1", "event_time": at,
        "occurrence": {
            "ticker": "SUGP", "event_id": "event-1", "signal_id": "event-1",
            "effective_at": at, "event_time": at,
            "evidence": {"market.last_price": 3.83, "session.phase": "premarket"},
            "field_evidence": {
                "market.last_price@1s": {
                    "field_ref": "market.last_price", "interval": "1s",
                    "aggregation": "", "value": 3.83,
                    "available_at": "2026-08-21T08:10:00+00:00", "null_reason": None,
                },
                "quote.bid_price": {
                    "field_ref": "quote.bid_price", "interval": "",
                    "aggregation": "", "value": 3.82,
                    "available_at": at, "null_reason": None,
                },
            },
        },
    }


class _MemoryClient:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {table.name: [] for table in ACTIVATION_TABLES}
        self.inserts: list[str] = []

    def execute(self, sql: str) -> str:
        if sql.startswith("INSERT INTO arte."):
            name = sql.split("arte.", 1)[1].split(" ", 1)[0]
            self.inserts.append(name)
            self.rows[name].extend(json.loads(line) for line in sql.split("FORMAT JSONEachRow\n", 1)[1].splitlines())
            return ""
        if sql.startswith("SELECT * FROM arte."):
            name = sql.split("arte.", 1)[1].split(" ", 1)[0]
            identity = dict(re.findall(r"(run_id|session_date|run_plan_id|ticker|event_id)='([^']*)'", sql))
            return "\n".join(json.dumps(row) for row in self.rows[name]
                             if all(str(row[key]) == value for key, value in identity.items()))
        raise AssertionError(sql)


class _Keeper:
    current = True

    def portfolio_admission_lease_is_current(self, resource_id, *, owner_id, epoch):
        assert resource_id == "activation:2026-08-21:plan-1:SUGP"
        assert owner_id == "worker-1" and epoch == 1
        return self.current


def _publish(client, projected, keeper=None):
    return publish_activation(client, projected, keeper=keeper or _Keeper(),
                              owner_id="worker-1", epoch=1)


class ActivationProjectionTests(unittest.TestCase):
    def test_restored_evidence_preserves_strategy_observation(self) -> None:
        source = _delivery()
        projected = project_activation(source)
        restored = restore_activation(projected)
        original_observation = strategy_observation_from_signal_occurrence(source["occurrence"])
        restored_observation = strategy_observation_from_signal_occurrence(restored["occurrence"])
        self.assertEqual(restored_observation, original_observation)
        self.assertEqual(restored["run_plan_id"], source["run_plan_id"])
        self.assertEqual(restored["occurrence"]["field_evidence"], source["occurrence"]["field_evidence"])

    def test_nested_evidence_fails_closed(self) -> None:
        source = _delivery()
        source["occurrence"]["evidence"]["market.last_price"] = {"value": 3.83}
        with self.assertRaisesRegex(ValueError, "unsupported dict"):
            project_activation(source)

    def test_unmodeled_field_evidence_fails_closed(self) -> None:
        source = _delivery()
        source["occurrence"]["field_evidence"]["quote.bid_price"]["source_revision"] = "2"
        with self.assertRaisesRegex(ValueError, "unmodeled field"):
            project_activation(source)

    def test_identity_mismatch_fails_closed(self) -> None:
        source = _delivery()
        source["event_id"] = "other"
        with self.assertRaisesRegex(ValueError, "identities differ"):
            project_activation(source)

    def test_delivery_time_mismatch_fails_closed(self) -> None:
        source = _delivery()
        source["event_time"] = "2026-08-21T08:10:02+00:00"
        with self.assertRaisesRegex(ValueError, "delivery time"):
            project_activation(source)

    def test_future_evidence_fails_closed(self) -> None:
        source = _delivery()
        source["occurrence"]["field_evidence"]["quote.bid_price"]["available_at"] = "2026-08-21T08:10:02+00:00"
        with self.assertRaisesRegex(ValueError, "availability must be causal"):
            project_activation(source)

    def test_typed_publication_and_verified_recovery(self) -> None:
        source = _delivery()
        client = _MemoryClient()
        projected = project_activation(source)
        digest = _publish(client, projected)
        self.assertEqual(digest, prepare_activation_rows(projected)["trading_activation_v1"][0]["content_hash"])
        restored = load_activation(client, session_date=date(2026, 8, 21),
                                   run_plan_id="plan-1", ticker="SUGP", event_id="event-1")
        for key, field in source["occurrence"]["field_evidence"].items():
            recovered = restored["occurrence"]["field_evidence"][key]
            self.assertEqual(recovered["value"], field["value"])
            self.assertEqual(datetime.fromisoformat(recovered["available_at"]),
                             datetime.fromisoformat(field["available_at"]))
        recovered_observation = strategy_observation_from_signal_occurrence(restored["occurrence"])
        original_observation = strategy_observation_from_signal_occurrence(source["occurrence"])
        self.assertEqual((recovered_observation.ticker, recovered_observation.observed_at,
                          recovered_observation.price, recovered_observation.bid,
                          recovered_observation.changed_source_ids),
                         (original_observation.ticker, original_observation.observed_at,
                          original_observation.price, original_observation.bid,
                          original_observation.changed_source_ids))
        insert_count = len(client.inserts)
        self.assertEqual(_publish(client, projected), digest)
        self.assertEqual(len(client.inserts), insert_count)

    def test_partial_or_tampered_rows_fail_recovery(self) -> None:
        client = _MemoryClient()
        projected = project_activation(_delivery())
        _publish(client, projected)
        query = dict(session_date=date(2026, 8, 21), run_plan_id="plan-1", ticker="SUGP", event_id="event-1")
        client.rows["trading_activation_commit_v1"].clear()
        with self.assertRaisesRegex(RuntimeError, "lacks a committed fence"):
            load_activation(client, **query)
        _publish(client, projected)
        client.rows["trading_activation_evidence_v1"][0]["value_float"] = 99.0
        with self.assertRaisesRegex(RuntimeError, "hash"):
            load_activation(client, **query)

    def test_uncommitted_partial_evidence_is_repaired_by_same_identity(self) -> None:
        client = _MemoryClient()
        projected = project_activation(_delivery())
        prepared = prepare_activation_rows(projected)
        client.rows["trading_activation_v1"].extend(prepared["trading_activation_v1"])
        client.rows["trading_activation_evidence_v1"].append(
            prepared["trading_activation_evidence_v1"][0])
        _publish(client, projected)
        restored = load_activation(client, session_date=date(2026, 8, 21),
                                   run_plan_id="plan-1", ticker="SUGP", event_id="event-1")
        self.assertEqual(restored["occurrence"]["evidence"],
                         _delivery()["occurrence"]["evidence"])
        self.assertEqual(len(client.rows["trading_activation_evidence_v1"]), 2)

    def test_keeper_claim_is_required_before_insertion(self) -> None:
        client = _MemoryClient()
        keeper = _Keeper()
        keeper.current = False
        with self.assertRaisesRegex(RuntimeError, "Keeper claim"):
            _publish(client, project_activation(_delivery()), keeper)
        self.assertFalse(client.inserts)

    def test_schema_is_registered_for_operator_preflight(self) -> None:
        from src.trading_runtime.arte_journal_schema import TABLES
        names = {table.name for table in TABLES}
        self.assertTrue({table.name for table in ACTIVATION_TABLES} <= names)
        self.assertTrue(all("live_market_ssd" in table.ddl() for table in ACTIVATION_TABLES))

    def test_decimal_bool_and_string_use_distinct_typed_columns(self) -> None:
        source = _delivery()
        source["occurrence"]["evidence"].update({
            "decimal": Decimal("12.3400"), "flag": True, "label": "open",
        })
        rows = prepare_activation_rows(project_activation(source))["trading_activation_evidence_v1"]
        by_key = {row["field_key"]: row for row in rows}
        self.assertEqual(by_key["decimal"]["value_decimal"], "12.340000000000000000")
        self.assertIsNone(by_key["decimal"]["value_text"])
        self.assertEqual(by_key["flag"]["value_bool"], 1)
        self.assertEqual(by_key["label"]["value_text"], "open")
        client = _MemoryClient()
        _publish(client, project_activation(source))
        restored = load_activation(client, session_date=date(2026, 8, 21),
                                   run_plan_id="plan-1", ticker="SUGP", event_id="event-1")
        evidence = restored["occurrence"]["evidence"]
        self.assertEqual(evidence["decimal"], Decimal("12.340000000000000000"))
        self.assertIs(evidence["flag"], True)
        self.assertEqual(evidence["label"], "open")


if __name__ == "__main__":
    unittest.main()
