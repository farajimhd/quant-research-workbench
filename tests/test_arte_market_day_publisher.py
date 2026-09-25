from __future__ import annotations

from copy import deepcopy
import json

import pytest

from src.trading_runtime.arte_market_day_certification import TABLES
from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperAuthority
from src.trading_runtime.arte_market_day_publisher import publish_market_day_certificate
from test_arte_market_day_certification import BUILD, DAY, inventory
from test_arte_market_day_keeper import FakeKeeper


class FakeClickHouse:
    def __init__(self):
        self.rows = {table.name: [] for table in TABLES}
        self.inserts = []
        self.after_insert = None
        self.disk = "live_market_ssd"

    def execute(self, sql):
        assert sql.startswith("SELECT ")
        if "FROM system.tables" in sql:
            return "\n".join(json.dumps(dict(name=table.name,
                storage_policy="live_market_ssd")) for table in TABLES)
        if "FROM system.parts" in sql:
            return "\n".join(json.dumps(dict(table=name, disk_name=self.disk))
                for name, rows in self.rows.items() if rows)
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
    publish_market_day_certificate(client, keeper, claim, prepared, sessions=(DAY,))
    assert client.inserts == [table.name for table in TABLES]
    assert keeper.load(BUILD) is not None
    # Exact already-persisted retry is idempotent; it cannot append duplicates.
    publish_market_day_certificate(client, keeper, claim, prepared, sessions=(DAY,))
    assert client.inserts == [table.name for table in TABLES]


def test_stale_owner_after_child_insert_never_reaches_fence_or_attestation() -> None:
    client, store, keeper, claim = setup()
    def expire(_):
        holder = next(path for path in store.rows if path.endswith("/holder"))
        del store.rows[holder]
    client.after_insert = expire
    with pytest.raises(RuntimeError, match="claim changed"):
        publish_market_day_certificate(client, keeper, claim,
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
        publish_market_day_certificate(client, keeper, claim,
                                       inventory(), sessions=(DAY,))
    assert keeper.load(BUILD) is None
    client, _, keeper, claim = setup()
    client.disk = "default"
    with pytest.raises(RuntimeError, match="outside live_market_ssd"):
        publish_market_day_certificate(client, keeper, claim,
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
        publish_market_day_certificate(client, keeper, claim,
                                       inventory(), sessions=(DAY,))
    assert keeper.load(BUILD) is not None
