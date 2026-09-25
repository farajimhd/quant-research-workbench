from __future__ import annotations

from copy import deepcopy
import json
import re

import pytest

from src.trading_runtime.arte_market_day_certification import TABLES
from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperAuthority
from src.trading_runtime.arte_market_day_publisher import publish_market_day_certificate
from src.trading_runtime.arte_market_day_source_plan import (
    TABLES as SOURCE_TABLES, recover_source_plan,
)
from test_arte_market_day_certification import BUILD, DAY, inventory
from test_arte_market_day_keeper import FakeKeeper


@pytest.fixture(autouse=True)
def canonical_source(monkeypatch):
    from scripts import build_market_day
    prepared = inventory()
    source_rows = {table.name: prepared[table.name] for table in SOURCE_TABLES}
    pinned = recover_source_plan(source_rows, BUILD,
        expected_hash=prepared["market_day_build_header_v1"][0]["source_plan_hash"])
    monkeypatch.setattr(build_market_day, "source_plan", lambda _client, _args: deepcopy(pinned))


class FakeClickHouse:
    def __init__(self):
        self.rows = {table.name: [] for table in TABLES}
        self.inserts = []
        self.after_insert = None
        self.disk = "live_market_ssd"

    def execute(self, sql):
        assert sql.startswith("SELECT ")
        if "FROM system.storage_policies" in sql:
            return json.dumps({"disks": ["live_market_ssd"]})
        selected = set(re.findall(r"'(market_day_[^']+)'", sql))
        if "FROM system.tables" in sql:
            detailed = "engine,storage_policy,partition_key,sorting_key" in sql
            return "\n".join(json.dumps(dict(name=table.name,
                storage_policy="live_market_ssd",
                **(dict(engine="MergeTree", partition_key=table.partition,
                        sorting_key=table.order.replace(",", ", ")) if detailed else {})))
                for table in TABLES if table.name in selected)
        if "FROM system.columns" in sql:
            return "\n".join(json.dumps(dict(table=table.name, name=name, type=kind))
                for table in TABLES if table.name in selected
                for name, kind in table.columns)
        if "FROM system.parts" in sql:
            if "disk_name!='live_market_ssd'" in sql and self.disk == "live_market_ssd":
                return ""
            return "\n".join(json.dumps(dict(table=name, disk_name=self.disk))
                for name, rows in self.rows.items() if rows and name in selected)
        name = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
        columns = sql.split("SELECT ", 1)[1].split(" FROM", 1)[0].split(",")
        return "\n".join(json.dumps({key: row[key] for key in columns})
                         for row in self.rows[name])

    def insert_typed_rows(self, name, rows):
        self.inserts.append(name)
        self.rows[name].extend(deepcopy(rows))
        if self.after_insert:
            self.after_insert(name)


def setup():
    store = FakeKeeper()
    keeper = MarketDayKeeperAuthority(store)
    claim = keeper.acquire(BUILD, "worker-a")
    assert claim is not None
    return FakeClickHouse(), store, keeper, claim


def test_publishes_fence_last_and_attests_only_exact_readback() -> None:
    client, _, keeper, claim = setup()
    prepared = {name: tuple(rows) for name, rows in inventory().items()}
    publish_market_day_certificate(client, object(), keeper, claim, prepared, sessions=(DAY,))
    expected_inserts = [table.name for table in TABLES if prepared[table.name]]
    assert client.inserts == expected_inserts
    assert keeper.load(BUILD) is not None
    # Exact already-persisted retry is idempotent; it cannot append duplicates.
    publish_market_day_certificate(client, object(), keeper, claim, prepared, sessions=(DAY,))
    assert client.inserts == expected_inserts


def test_clickhouse_numeric_and_utc_wire_format_preserves_certificate_hashes() -> None:
    class ClickHouseWire(FakeClickHouse):
        def execute(self, sql):
            result = super().execute(sql)
            if "FROM arte.market_day_" not in sql:
                return result
            name = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
            kinds = dict(next(table for table in TABLES if table.name == name).columns)
            rows = [json.loads(line) for line in result.splitlines() if line.strip()]
            for row in rows:
                for column, value in row.items():
                    if kinds[column] == "UInt64" and value is not None:
                        row[column] = str(value)
                    elif kinds[column] == "DateTime64(6, 'UTC')":
                        row[column] = value.replace("T", " ").replace("+00:00", "")
            return "\n".join(json.dumps(row) for row in rows)

    _, _, keeper, claim = setup()
    client = ClickHouseWire()
    prepared = inventory()
    publish_market_day_certificate(client, object(), keeper, claim,
                                   prepared, sessions=(DAY,))
    assert keeper.load(BUILD) is not None
    publish_market_day_certificate(client, object(), keeper, claim,
                                   prepared, sessions=(DAY,))
    assert len(client.inserts) == len([name for name, rows in prepared.items() if rows])


def test_interrupted_partial_stage_insert_resumes_only_missing_rows() -> None:
    class Interrupted(FakeClickHouse):
        fail_once = True

        def insert_typed_rows(self, name, rows):
            if name == "market_day_stage_certificate_v1" and self.fail_once:
                self.fail_once = False
                self.inserts.append(name)
                self.rows[name].append(deepcopy(rows[0]))
                raise OSError("acknowledgement lost after partial insert")
            super().insert_typed_rows(name, rows)

    _, _, keeper, claim = setup()
    client = Interrupted()
    prepared = inventory()
    with pytest.raises(OSError, match="acknowledgement lost"):
        publish_market_day_certificate(client, object(), keeper, claim,
                                       prepared, sessions=(DAY,))
    assert keeper.load(BUILD) is None
    assert len(client.rows["market_day_stage_certificate_v1"]) == 1
    publish_market_day_certificate(client, object(), keeper, claim,
                                   prepared, sessions=(DAY,))
    assert client.rows["market_day_stage_certificate_v1"] == list(
        prepared["market_day_stage_certificate_v1"])
    assert keeper.load(BUILD) is not None


def test_partial_retry_rejects_duplicate_or_foreign_rows() -> None:
    client, _, keeper, claim = setup()
    prepared = inventory()
    name = "market_day_stage_certificate_v1"
    client.rows[name] = [deepcopy(prepared[name][0]), deepcopy(prepared[name][0])]
    with pytest.raises(RuntimeError, match="exact stored inventory"):
        publish_market_day_certificate(client, object(), keeper, claim,
                                       prepared, sessions=(DAY,))
    assert not client.inserts
    assert keeper.load(BUILD) is None


def test_stale_owner_after_child_insert_never_reaches_fence_or_attestation() -> None:
    client, store, keeper, claim = setup()
    def expire(_):
        holder = next(path for path in store.rows if path.endswith("/holder"))
        del store.rows[holder]
    client.after_insert = expire
    with pytest.raises(RuntimeError, match="claim changed"):
        publish_market_day_certificate(client, object(), keeper, claim,
                                       inventory(), sessions=(DAY,))
    assert not client.rows["market_day_build_fence_v1"]
    assert keeper.load(BUILD) is None


def test_delayed_conflicting_child_or_bad_part_blocks_attestation() -> None:
    client, _, keeper, claim = setup()
    def delayed(name):
        if name == "market_day_build_fence_v1":
            client.rows["market_day_stage_certificate_v1"].append(
                deepcopy(client.rows["market_day_stage_certificate_v1"][0]))
    client.after_insert = delayed
    with pytest.raises(RuntimeError, match="exact stored inventory"):
        publish_market_day_certificate(client, object(), keeper, claim,
                                       inventory(), sessions=(DAY,))
    assert keeper.load(BUILD) is None
    client, _, keeper, claim = setup()
    client.disk = "default"
    with pytest.raises(RuntimeError, match="outside live_market_ssd"):
        publish_market_day_certificate(client, object(), keeper, claim,
                                       inventory(), sessions=(DAY,))
    assert not client.rows["market_day_build_fence_v1"]
    assert keeper.load(BUILD) is None


def test_late_duplicate_after_cas_is_not_a_successful_receipt() -> None:
    client, _, keeper, claim = setup()
    attest = keeper.attest
    def late(*args, **kwargs):
        proof = attest(*args, **kwargs)
        client.rows["market_day_stage_certificate_v1"].append(
            deepcopy(client.rows["market_day_stage_certificate_v1"][0]))
        return proof
    keeper.attest = late
    with pytest.raises(RuntimeError, match="exact stored inventory"):
        publish_market_day_certificate(client, object(), keeper, claim,
                                       inventory(), sessions=(DAY,))
    assert keeper.load(BUILD) is not None


def test_source_plan_layout_drift_blocks_first_insert() -> None:
    class DriftClient(FakeClickHouse):
        def execute(self, sql):
            result = super().execute(sql)
            if "FROM system.tables" in sql and "sorting_key" in sql:
                rows = [json.loads(line) for line in result.splitlines()]
                rows[0]["partition_key"] = "tuple()"
                return "\n".join(json.dumps(row) for row in rows)
            return result

    _, _, keeper, claim = setup()
    client = DriftClient()
    with pytest.raises(RuntimeError, match="Source-plan table layout differs"):
        publish_market_day_certificate(client, object(), keeper, claim,
                                       inventory(), sessions=(DAY,))
    assert not client.inserts
    assert keeper.load(BUILD) is None


def test_canonical_source_drift_blocks_every_insert_and_attestation(monkeypatch) -> None:
    from scripts import build_market_day
    client, _, keeper, claim = setup()
    pinned = inventory()
    source_rows = {table.name: pinned[table.name] for table in SOURCE_TABLES}
    changed = recover_source_plan(source_rows, BUILD,
        expected_hash=pinned["market_day_build_header_v1"][0]["source_plan_hash"])
    changed["rules"][0]["modifier_int"] += 1
    monkeypatch.setattr(build_market_day, "source_plan", lambda _client, _args: changed)
    with pytest.raises(RuntimeError, match="differs before publication"):
        publish_market_day_certificate(client, object(), keeper, claim,
                                       pinned, sessions=(DAY,))
    assert not client.inserts
    assert keeper.load(BUILD) is None
